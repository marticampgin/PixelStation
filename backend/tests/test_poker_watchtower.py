import asyncio
import json
import threading

from fastapi.testclient import TestClient
from test_harness import wait_evaluation

from pixel_station.app import create_app
from pixel_station.config import AppSettings
from pixel_station.database import AgentRun
from pixel_station.evaluations import fixture_identity
from pixel_station.harness import baseline_compatible
from pixel_station.poker_evaluations import fixtures
from pixel_station.watchtower import measurements, public_run


def test_poker_only_native_scope_uses_production_path_and_freezes_settings(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class FixtureModel:
        calls = 0

        async def stream(self, *args, **kwargs):
            raise AssertionError("Chat native scope was not selected")
            yield

        async def structured(self, model, messages, schema, **kwargs):
            self.calls += 1
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
            view = json.loads(messages[-1]["content"].split("\nPrevious action failed", 1)[0])
            hole = set(view["seats"][view["actor"]]["hole"])
            scenario = next(
                row
                for row in fixtures()["scenarios"]
                if row["stage"] == view["stage"] and set(row["hole"]) == hole
            )
            return schema(**scenario["admissible_examples"][0])

    model = FixtureModel()
    app = create_app(tmp_path, llm=model, discover=False)
    settings = AppSettings(harness_enabled=False)
    settings.roles["primary_chat"] = "fixture:model"
    app.state.set_settings(settings)
    with TestClient(app) as client:
        started = client.post("/api/harness/evaluations", json={"poker_native": True})
        assert started.status_code == 202
        assert entered.wait(5)
        assert client.put("/api/settings", json=settings.model_dump()).status_code == 409
        assert (
            client.post("/api/harness/evaluations", json={"poker_native": True}).status_code == 409
        )
        release.set()
        report = wait_evaluation(client, started.json()["id"])
        assert report["counts"] == {"PASS": 19, "SKIP": 2}
        assert report["poker_native_requested"] is True and report["native_requested"] is False
        assert model.calls == 6
        assert "decision_source_sha256" in report["fixture"]["poker"]
        with app.state.database.session() as session:
            assert not list(session.query(AgentRun))  # Synthetic probes are not production play.


def test_passive_poker_counts_distinguish_attempted_and_final_actions(tmp_path):
    app = create_app(tmp_path, discover=False)
    with app.state.database.session() as session:
        for action, raw, fallback in (("fold", ["all_in", "all_in"], 1), ("raise", ["raise"], 0)):
            session.add(
                AgentRun(
                    route="poker_bot",
                    status="complete",
                    evidence={
                        "metrics": {
                            "version": 1,
                            "strategy_version": 1,
                            "strategy_stage": "preflop",
                            "selected_action": action,
                            "raw_actions": raw,
                            "fallback_count": fallback,
                            "validation_rejections": 2 if fallback else 0,
                        }
                    },
                )
            )
        # A historical Poker run has no new action measurements; do not invent its action.
        session.add(AgentRun(route="poker_bot", status="complete", evidence={}))
        session.commit()
        stats = measurements(session, app.state.settings())["poker_strategy"]
        assert stats["sample_count"] == 2 and stats["actions"] == {"fold": 1, "raise": 1}
        assert stats["native_without_fallback"] == 1 and stats["fallback_decisions"] == 1
        assert stats["preflop_all_ins"] == 0 and stats["raw_preflop_all_in_attempts"] == 2
        assert stats["validation_rejections"] == 2
        encoded = json.dumps([public_run(row) for row in session.query(AgentRun)])
        assert "all_in" in encoded and "reason_short" not in encoded


def test_poker_scope_and_policy_identity_prevent_incompatible_baselines():
    identity = fixture_identity()
    report = {
        "kind": "evaluation",
        "status": "COMPLETE",
        "fixture": identity,
        "native_requested": False,
        "poker_native_requested": False,
    }
    assert baseline_compatible(report, report)
    changed = {**identity, "poker": {**identity["poker"], "decision_source_sha256": "changed"}}
    assert not baseline_compatible(report, {**report, "fixture": changed})
    assert not baseline_compatible(report, {**report, "poker_native_requested": True})
