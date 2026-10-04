import asyncio
import json
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from pixel_station.app import create_app
from pixel_station.config import AppSettings
from pixel_station.database import (
    AgentRun,
    Attachment,
    Conversation,
    FrictionEvent,
    HarnessReport,
    Memory,
    now,
)
from pixel_station.evaluations import (
    RUNNER_VERSION,
    deterministic_cases,
    fixtures,
    native_cases,
    validate_file_answer,
    validate_plan_text,
)
from pixel_station.harness import baseline_compatible, record_observation
from pixel_station.observability import observe_provider, provider_observations, run_metrics
from pixel_station.providers import UnsupportedToolCall
from pixel_station.watchtower import distribution, measurements


class NoInference:
    async def stream(self, *args, **kwargs):
        raise AssertionError("Deterministic evaluation must not invoke inference")
        yield ""


def wait_evaluation(client, identity):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        row = client.get(f"/api/harness/evaluations/{identity}").json()
        if row["report"]["status"] != "RUNNING":
            return row["report"]
        time.sleep(0.02)
    raise AssertionError("Evaluation did not finish")


def test_measurements_outcomes_percentiles_and_missing_native_metadata(tmp_path):
    app = create_app(tmp_path, llm=NoInference(), discover=False)
    for status, latency, repairs, fallback in (
        ("complete", 100, 0, 0),
        ("error", 500, 1, 0),
        ("interrupted", 200, 0, 1),
    ):
        record_observation(
            app,
            route="normal_chat",
            model="fixture:model",
            status=status,
            latency_ms=latency,
            metrics={"repair_count": repairs, "fallback_count": fallback},
        )
    with app.state.database.session() as session:
        measured = measurements(session, app.state.settings())
        for row in session.scalars(select(AgentRun)):
            started = datetime.fromisoformat(row.started_at)
            finished = datetime.fromisoformat(row.finished_at)
            assert started <= finished
            assert (finished - started).total_seconds() * 1000 == row.latency_ms
            assert "derived" in row.evidence["metrics"]["timing_source"]
    assert measured["outcomes"] == {"complete": 1, "error": 1, "interrupted": 1}
    assert measured["completion_rate"] == 1 / 3
    assert measured["latency_ms"]["median"] == 200
    assert measured["latency_ms"]["p95"] == 500
    assert measured["repair_attempts"] == measured["fallbacks"] == 1
    assert measured["native_usage"]["generation_token_samples"] == 0
    assert measured["native_usage"]["tokens_per_second"]["median"] is None
    assert distribution([])["p95"] is None


async def test_provider_measurements_are_scoped_and_never_collect_reasoning():
    async def observe(identity):
        collected = []
        token = provider_observations.set(collected)
        try:
            await asyncio.sleep(0)
            observe_provider(
                {
                    "eval_count": 10,
                    "eval_duration": 2_000_000_000,
                    "prompt_eval_count": 12,
                    "thinking": "PRIVATE",
                    "message": {"content": "PRIVATE"},
                },
                "stream",
                identity,
            )
        finally:
            provider_observations.reset(token)
        return collected

    first, second = await asyncio.gather(observe("one"), observe("two"))
    assert [row["model"] for row in first] == ["one"]
    assert [row["model"] for row in second] == ["two"]
    assert first[0]["tokens_per_second"] == 5
    assert "PRIVATE" not in json.dumps(first)
    assert provider_observations.get() is None


def test_diagnostics_redact_content_by_default_and_bound_opt_in(tmp_path):
    app = create_app(tmp_path, llm=NoInference(), discover=False)
    with app.state.database.session() as session:
        session.add(
            AgentRun(
                route="normal_chat",
                model="fixture",
                status="error",
                finished_at=now(),
                evidence={
                    "traces": [{"content": "PRIVATE PROMPT"}],
                    "metrics": {"repair_count": 1},
                },
            )
        )
        session.add(
            FrictionEvent(
                kind="negative_feedback",
                details="PRIVATE EMAIL",
                regression={"input": "PRIVATE REGRESSION"},
            )
        )
        session.add(
            HarnessReport(
                report={
                    "kind": "evaluation",
                    "status": "COMPLETE",
                    "cases": [
                        {"id": "native_plan", "status": "FAIL", "private_output": "PRIVATE ANSWER"}
                    ],
                }
            )
        )
        session.commit()
    with TestClient(app) as client:
        response = client.get("/api/harness/diagnostics")
        assert response.headers["content-disposition"].startswith("attachment")
        assert "PRIVATE" not in response.text
        assert response.json()["content_included"] is False
        included = client.get("/api/harness/diagnostics?include_content=true").text
        assert "PRIVATE PROMPT" in included and "PRIVATE REGRESSION" in included
        assert "PRIVATE" not in client.get("/api/harness").text


def test_legacy_default_zero_latency_is_unavailable_not_a_measured_sample(tmp_path):
    app = create_app(tmp_path, llm=NoInference(), discover=False)
    with app.state.database.session() as session:
        session.add(AgentRun(route="normal_chat", status="interrupted", finished_at=now()))
        session.commit()
        measured = measurements(session, app.state.settings())
    assert measured["terminal_runs"] == 1
    assert measured["latency_ms"]["sample_count"] == 0
    assert measured["latency_ms"]["median"] is None
    assert measured["groups"][0]["sample_count"] == 0


def test_versioned_deterministic_suite_uses_real_core_and_detects_injected_failure(monkeypatch):
    cases = deterministic_cases()
    assert len(cases) == 13
    assert {case["status"] for case in cases} == {"PASS"}
    from pixel_station import evaluations

    monkeypatch.setattr(
        evaluations, "route_prompt", lambda text: type("Route", (), {"intent": "incorrect"})()
    )
    repeated = deterministic_cases()
    routing = next(case for case in repeated if case["id"] == "routing")
    assert routing["status"] == "FAIL"
    assert routing["measurements"]["mismatches"]


def test_evaluation_api_is_persistent_isolated_and_native_is_explicit(tmp_path):
    app = create_app(tmp_path, llm=NoInference(), discover=False)
    with TestClient(app) as client:
        started = client.post("/api/harness/evaluations", json={})
        assert started.status_code == 202
        result = wait_evaluation(client, started.json()["id"])
        assert result["status"] == "COMPLETE"
        assert result["counts"] == {"PASS": 19, "SKIP": 8}, [
            case for case in result["cases"] if case["status"] not in {"PASS", "SKIP"}
        ]
        assert result["native_requested"] is False
        assert result["poker_native_requested"] is False
        assert result["fixture"]["poker"]["version"] == "pixel-station-poker-strategy-v1"
        assert result["runner_version"] == RUNNER_VERSION
        with app.state.database.session() as session:
            legacy = {key: value for key, value in result.items() if key != "runner_version"}
            legacy_row = HarnessReport(report=legacy)
            session.add(legacy_row)
            session.commit()
            legacy_id = legacy_row.id
        second = client.post("/api/harness/evaluations", json={"native": False}).json()
        repeated = wait_evaluation(client, second["id"])
        assert repeated["baseline_id"] == started.json()["id"]
        assert repeated["baseline_id"] != legacy_id
        assert not any(case["regressed"] for case in repeated["comparison"])
        with app.state.database.session() as session:
            for model in (Conversation, Attachment, Memory):
                assert session.scalar(select(func.count()).select_from(model)) == 0
    restored = create_app(tmp_path, llm=NoInference(), discover=False)
    with TestClient(restored) as client:
        assert (
            client.get(f"/api/harness/evaluations/{started.json()['id']}").json()["report"][
                "status"
            ]
            == "COMPLETE"
        )


def test_native_constraint_gate_rejects_overlong_step_and_stale_artifact_claim():
    fixture = fixtures()["plan"]
    valid = "1. Group related files.\n2. Name folders consistently.\n3. Archive unused items."
    assert validate_plan_text(valid, fixture)["passed"]
    assert not validate_plan_text(valid + "\nCreated fixture-first.csv", fixture)["passed"]
    assert not validate_plan_text(
        "1. " + "word " * 15 + "\n2. Name folders.\n3. Archive files.", fixture
    )["passed"]


def test_repair_metrics_count_actual_retry_not_repeated_validation_failure():
    measured = run_metrics(
        AppSettings(),
        [
            {"validation": "unsupported_tool_protocol", "retry": 1},
            {"validation": "unsupported_tool_protocol", "retry": 2},
            {"validation": "research_plan_error", "retry": 0},
            {"validation": "research_plan_error", "retry": 1},
        ],
        "",
        [],
    )
    assert measured["repair_count"] == 2
    assert measured["validation_rejections"] == 4
    assert measured["tool_steps"] == 0


def test_native_probe_is_manual_read_only_records_failure_and_freezes_configuration(tmp_path):
    started, release = threading.Event(), threading.Event()

    class ProbeFixture:
        async def stream(self, model, messages, **kwargs):
            started.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
            if "verification code" in messages[-1]["content"]:
                yield "violet-42"
            else:
                yield "Created fixture-first.csv"  # A real gate must reject this simulated defect.

    app = create_app(tmp_path, llm=ProbeFixture(), discover=False)
    settings = app.state.settings()
    settings.roles["primary_chat"] = "fixture:model"
    app.state.set_settings(settings)
    with TestClient(app) as client:
        started_job = client.post("/api/harness/evaluations", json={"native": True}).json()
        assert started.wait(timeout=5)
        changed = client.put("/api/settings", json={**settings.model_dump(), "retrieval_count": 8})
        assert changed.status_code == 409
        assert client.post("/api/harness/evaluations", json={}).status_code == 409
        release.set()
        result = wait_evaluation(client, started_job["id"])
        assert result["outcome"] == "FAIL"
        assert result["counts"] == {"PASS": 20, "FAIL": 1, "SKIP": 6}
        assert "Created fixture-first.csv" not in json.dumps(result)
        assert result["configuration"]["retrieval_count"] == 4
        assert client.put("/api/settings", json=settings.model_dump()).status_code == 200
        with app.state.database.session() as session:
            for model in (Conversation, Attachment, Memory, AgentRun):
                assert session.scalar(select(func.count()).select_from(model)) == 0


async def test_cancellation_before_retrieval_does_not_report_lexical_fallback(
    tmp_path, monkeypatch
):
    from pixel_station import chat
    from pixel_station.chat import MessageInput, generate_response

    app = create_app(tmp_path, llm=NoInference(), discover=False)
    settings = app.state.settings()
    settings.roles["embedding"] = "fixture:embedding"
    app.state.set_settings(settings)
    with app.state.database.session() as session:
        conversation = Conversation(title="fixture")
        session.add(conversation)
        session.commit()
        identity = conversation.id
    started = asyncio.Event()

    async def queued_embedding(*args):
        started.set()
        await asyncio.sleep(30)

    monkeypatch.setattr(chat, "embed_query", queued_embedding)

    async def consume():
        async for _ in generate_response(app, identity, MessageInput(content="Hello")):
            pass
        assert provider_observations.get() is None

    task = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    await asyncio.wait_for(task, 2)
    assert provider_observations.get() is None
    with app.state.database.session() as session:
        run = session.scalar(select(AgentRun))
        assert run.status == "interrupted"
        assert run.evidence["metrics"]["fallback_count"] == 0


@pytest.mark.parametrize("protocol", ["native_tool_calls", "split_bracket_call"])
async def test_native_probes_use_production_bounded_repair_and_replace_partial_output(
    tmp_path, protocol
):
    requests = []
    file_attempts = 0

    class RepairFixture:
        async def stream(self, model, messages, **kwargs):
            nonlocal file_attempts
            requests.append({"messages": messages, "kwargs": kwargs})
            if "verification code" in messages[-1]["content"]:
                file_attempts += 1
                if file_attempts == 1:
                    yield "I'll read the notes. "
                    if protocol == "native_tool_calls":
                        raise UnsupportedToolCall("Native fixture requested a tool")
                    yield "[re"
                    yield "ad(path='fixture-notes.txt')]"
                else:
                    assert "The verification code is violet-42." in messages[0]["content"]
                    assert "LATEST USER REQUEST:" in messages[-1]["content"]
                    yield "violet-42"
            else:
                yield "1. Group related files.\n2. Name folders consistently.\n3. Archive unused items."

    app = create_app(tmp_path, llm=RepairFixture(), discover=False)
    snapshot = AppSettings(context_tokens=2048)
    snapshot.roles["primary_chat"] = "fixture:model"
    cases = await native_cases(app, snapshot)
    assert [case["status"] for case in cases] == ["PASS", "PASS"]
    file_case = cases[1]
    assert file_case["private_output"] == "violet-42"
    assert file_case["measurements"]["validation_rejections"] == 1
    assert file_case["measurements"]["repair_attempts"] == 1
    assert file_case["measurements"]["provider_call_attempts"] == 2
    assert file_case["measurements"]["stream_resets"] == 1
    assert file_case["measurements"]["terminal_metadata_samples"] == 0
    assert len(requests) == 3
    for request in requests:
        assert request["kwargs"]["options"] == {
            "num_ctx": 2048,
            "num_predict": 256,
            "temperature": 0,
        }
    assert provider_observations.get() is None


async def test_native_probe_fails_after_the_single_production_repair(tmp_path):
    attempts = 0

    class RejectFixture:
        async def stream(self, model, messages, **kwargs):
            nonlocal attempts
            if "verification code" in messages[-1]["content"]:
                attempts += 1
                yield "Unsupported partial claim. "
                raise UnsupportedToolCall("Native fixture requested a tool")
            yield "1. Group related files.\n2. Name folders consistently.\n3. Archive unused items."

    app = create_app(tmp_path, llm=RejectFixture(), discover=False)
    snapshot = AppSettings()
    snapshot.roles["primary_chat"] = "fixture:model"
    cases = await native_cases(app, snapshot)
    assert cases[1]["status"] == "FAIL"
    assert attempts == 2
    assert cases[1]["measurements"]["validation_rejections"] == 2
    assert cases[1]["measurements"]["repair_attempts"] == 1
    assert cases[1]["measurements"]["provider_call_attempts"] == 2
    assert cases[1]["private_output"] == ""
    assert provider_observations.get() is None


def test_problem_counts_use_the_latest_capped_friction_window(tmp_path):
    app = create_app(tmp_path, llm=NoInference(), discover=False)
    settings = app.state.settings()
    settings.harness_sample_limit = 100
    with app.state.database.session() as session:
        older = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        session.add_all(
            [FrictionEvent(kind="older", details="", created_at=older) for _ in range(100)]
        )
        session.add_all([FrictionEvent(kind="latest", details="") for _ in range(101)])
        session.commit()
        measured = measurements(session, settings)
    assert measured["problem_counts"] == {"latest": 100}


@pytest.mark.parametrize(
    "answer,passed",
    [
        ("violet-42", True),
        ("The verification code is **violet-42**. [fixture-notes.txt, L1]", True),
        ("Code: `violet-42`.", True),
        ("The code is not violet-42; it is red-99.", False),
        ("The code isn't violet-42.", False),
        ("violet-42 or red-99", False),
        ("The notes quote violet-42 but the code is unavailable.", False),
        ("violet-42 violet-42", False),
        ("violet-42 [not violet-42; it is red-99]", False),
    ],
)
def test_file_qa_gate_requires_one_affirmative_brief_answer(answer, passed):
    assert validate_file_answer(answer, fixtures()["file_qa"])["passed"] is passed


def test_native_baseline_requires_known_immutable_identity_and_matching_versions():
    report = {
        "kind": "evaluation",
        "status": "COMPLETE",
        "runner_version": RUNNER_VERSION,
        "fixture": {"version": "fixture", "sha256": "fixture-hash"},
        "native_requested": True,
        "configuration": {"roles": {"primary_chat": "local:alias"}},
        "versions": {"python": "3.12", "pixel-station": "0.1.0"},
        "native_runtime": {
            "provider": "OllamaProvider",
            "model": "local:alias",
            "ollama": "0.32.14",
            "digest": "1" * 64,
        },
    }
    assert baseline_compatible(report, report)
    for field, value in (
        ("digest", "2" * 64),
        ("digest", None),
        ("ollama", "0.32.15"),
        ("ollama", "not_available"),
    ):
        assert not baseline_compatible(
            report, {**report, "native_runtime": {**report["native_runtime"], field: value}}
        )
    assert not baseline_compatible(report, {**report, "native_runtime": None})
    assert not baseline_compatible(report, {**report, "versions": {"python": "3.13"}})
    assert not baseline_compatible(report, {**report, "runner_version": RUNNER_VERSION - 1})


def test_passive_reports_do_not_displace_the_completed_evaluation_baseline(tmp_path):
    app = create_app(tmp_path, llm=NoInference(), discover=False)
    with TestClient(app) as client:
        previous = client.post("/api/harness/evaluations", json={"native": False}).json()
        assert wait_evaluation(client, previous["id"])["status"] == "COMPLETE"
        with app.state.database.session() as session:
            session.add_all([HarnessReport(report={"kind": "passive"}) for _ in range(35)])
            session.commit()
        current = client.post("/api/harness/evaluations", json={"native": False}).json()
        assert wait_evaluation(client, current["id"])["baseline_id"] == previous["id"]
