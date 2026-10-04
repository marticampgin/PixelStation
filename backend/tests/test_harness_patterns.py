import json

import pytest
from fastapi.testclient import TestClient

from pixel_station.app import create_app
from pixel_station.config import AppSettings
from pixel_station.database import AgentRun, Database, FrictionEvent, now
from pixel_station.harness import create_report
from pixel_station.observability import run_metrics
from pixel_station.watchtower import failure_observations, public_report


def add_problem(session, *, route="web_research", model="fixture:model", code="search_engines_unavailable",
                tool="web_search", stage="tool_execution", private="PRIVATE EMAIL", traces=None):
    run = AgentRun(route=route, model=model, status="error", finished_at=now(), evidence={"traces": traces if traces is not None else [{
        "error": private, "error_code": code, "error_type": "IntegrationError", "tool": tool, "stage": stage,
    }]})
    session.add(run)
    session.flush()
    event = FrictionEvent(run_id=run.id, kind="response_error", details=private, regression={"input": private, "expected_route": route})
    session.add(event)
    session.flush()
    return run, event


def database(tmp_path):
    db = Database(tmp_path)
    db.migrate()
    return db


def test_exact_patterns_aggregate_repeated_structured_failures_without_parsing_private_text(tmp_path):
    with database(tmp_path).session() as session:
        first, _ = add_problem(session, private="PRIVATE EMAIL ONE")
        second, _ = add_problem(session, private="PRIVATE EMAIL TWO")
        report = create_report(session, AppSettings())
        patterns = public_report(report)["report"]["patterns"]
        assert len(patterns) == 1
        pattern = patterns[0]
        assert pattern["count"] == pattern["run_count"] == 2
        assert set(pattern["run_ids"]) == {first.id, second.id}
        assert pattern["error_codes"] == ["search_engines_unavailable"]
        assert pattern["failed_tools"] == ["web_search"]
        assert "actual SearXNG JSON search" in pattern["recommendation"]
        assert "genuine-empty" in pattern["regression_hint"]
        assert "PRIVATE" not in json.dumps(public_report(report))
        assert "PRIVATE EMAIL" in json.dumps(public_report(report, include_content=True))


@pytest.mark.parametrize("change", [
    {"route": "normal_chat"}, {"model": "other:model"}, {"code": "search_forbidden"},
    {"tool": "web_fetch"}, {"stage": "planning"},
])
def test_different_recorded_routes_models_errors_tools_and_stages_are_not_merged(tmp_path, change):
    with database(tmp_path).session() as session:
        add_problem(session)
        add_problem(session, **change)
        report = public_report(create_report(session, AppSettings()))["report"]
        assert report["pattern_count"] == 2
        assert len({pattern["id"] for pattern in report["patterns"]}) == 2
        assert all(pattern["count"] == 1 for pattern in report["patterns"])


def test_unknown_or_legacy_errors_are_unclassified_and_unknown_fields_do_not_leak(tmp_path):
    with database(tmp_path).session() as session:
        add_problem(session, traces=[{"error": "PRIVATE URL AND PROMPT", "error_code": "PRIVATE_CODE", "error_type": "PRIVATE_TYPE", "stage": "PRIVATE_STAGE", "tool": "PRIVATE_FILE"}])
        report = public_report(create_report(session, AppSettings()))["report"]
        pattern = report["patterns"][0]
        assert pattern["classification"] == "unclassified"
        assert pattern["error_codes"] == pattern["error_types"] == pattern["stages"] == pattern["failed_tools"] == []
        assert "unavailable evidence" in pattern["recommendation"]
        assert "PRIVATE" not in json.dumps(report)
        assert "Raw error text is not parsed" in report["pattern_method"]


def test_malformed_trace_fields_are_not_public_diagnostic_labels():
    run = AgentRun(route="normal_chat", evidence={"traces": [None, {"error_code": [], "error_type": {}, "tool": {}, "stage": [], "validation": [], "result": {"errors": [{"code": [], "tool": {}}]}}]})
    assert all(not values for values in failure_observations(run).values())


def test_pattern_counts_remain_exact_when_link_lists_are_capped(tmp_path):
    with database(tmp_path).session() as session:
        for _ in range(25):
            add_problem(session)
        pattern = public_report(create_report(session, AppSettings()))["report"]["patterns"][0]
        assert pattern["count"] == pattern["run_count"] == 25
        assert len(pattern["run_ids"]) == len(pattern["event_ids"]) == 20
        assert pattern["links_capped"] is True


def test_integrity_rejections_are_reproduction_candidates_not_claimed_root_causes(tmp_path):
    with database(tmp_path).session() as session:
        add_problem(session, route="gmail_send", code="attachment_changed", tool="gmail_send")
        report = public_report(create_report(session, AppSettings()))["report"]
        assert "expected integrity guard" in report["patterns"][0]["recommendation"]
        assert "reject before sending" in report["patterns"][0]["regression_hint"]
        assert "do not establish root cause" in report["interpretation"]


def test_run_links_are_real_and_default_exports_exclude_private_pattern_examples(tmp_path):
    app = create_app(tmp_path, discover=False)
    with app.state.database.session() as session:
        run, _ = add_problem(session)
        session.commit()
        identity = run.id
        create_report(session, app.state.settings())
    with TestClient(app) as client:
        response = client.get("/api/harness/runs/" + identity)
        assert response.status_code == 200 and response.json()["id"] == identity
        assert "PRIVATE" not in response.text
        assert "PRIVATE EMAIL" in client.get("/api/harness/runs/" + identity + "?include_content=true").text
        assert client.get("/api/harness/runs/missing").status_code == 404
        exported = client.get("/api/harness/diagnostics")
        assert exported.json()["content_included"] is False and "PRIVATE" not in exported.text
        assert "PRIVATE EMAIL" in client.get("/api/harness/diagnostics?include_content=true").text


def test_adaptive_repairs_count_actual_retry_and_no_repeat_rejection_without_another_retry():
    repaired = run_metrics(AppSettings(), [
        {"validation": "adaptive_action_error", "retry": 0, "error_code": "adaptive_action_invalid"},
        {"validation": "adaptive_action_error", "retry": 1, "error_code": "adaptive_action_invalid"},
    ], "", [])
    assert repaired["repair_count"] == 1 and repaired["validation_rejections"] == 2
    repeated = run_metrics(AppSettings(), [
        {"validation": "adaptive_action_error", "retry": 0, "error_code": "adaptive_repeated_action"},
    ], "", [])
    assert repeated["repair_count"] == 0 and repeated["validation_rejections"] == 1
