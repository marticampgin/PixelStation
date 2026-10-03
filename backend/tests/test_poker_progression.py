"""Public turn streams commit one decision, survive restart, and never disclose future cards."""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.requests import Request
from test_poker_api import PokerLLM, make_app

from pixel_station.database import AgentRun
from pixel_station.observability import observe_provider, provider_observations
from pixel_station.poker import act, new_game, public_view, run_bots


def stream_step(client, table):
    response = client.post(
        f"/api/poker/sessions/{table['id']}/steps",
        json={"expected_sequence": table["event_sequence"]},
    )
    assert response.status_code == 200, response.text
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[-1] == {"type": "done"}
    states = [event["state"] for event in events if event["type"] == "state"]
    assert len(states) == 1
    return states[0], events


def assert_private(view):
    assert "deck" not in view and "pending" not in view
    if view["stage"] != "showdown":
        assert all(not seat["hole"] for seat in view["seats"] if seat["index"] != 0)
    assert all("hole" not in event and "board" not in event for event in view["event_log"])


def test_six_seat_progressive_create_and_stream_one_actual_bot_action(tmp_path):
    llm = PokerLLM()
    app = make_app(tmp_path, llm)
    with TestClient(app) as client:
        table = client.post("/api/poker/sessions?progressive=true", json={"seats": 6}).json()
        assert len(table["seats"]) == 6 and table["actor"] == 3
        assert table["event_sequence"] == 2 and table["needs_step"]
        assert table["event_log"][0]["pot"] == 5
        assert table["event_log"][1]["pot"] == 15
        assert not llm.views
        after, events = stream_step(client, table)
        assert [event["type"] for event in events] == ["phase", "phase", "state", "done"]
        assert [event["phase"] for event in events if event["type"] == "phase"] == [
            "queued",
            "choosing",
        ]
        assert len(llm.views) == 1 and after["event_sequence"] == 3
        assert after["actor"] == 4 and after["board"] == [] and not after["winners"]
        assert after["event_log"][-1]["seat"] == 3
        for event in events:
            if "state" in event:
                assert_private(event["state"])
        assert events[0]["state"]["event_sequence"] == 2
        assert events[0]["state"]["event_log"][-1]["action"] == "big_blind"
        assert client.get(f"/api/poker/sessions/{table['id']}").json() == after


def test_human_duplicate_rejected_and_street_is_a_separate_saved_step(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        table = client.post("/api/poker/sessions?progressive=true", json={"seats": 2}).json()
        path = f"/api/poker/sessions/{table['id']}/actions?progressive=true"
        body = {"action": "call", "expected_sequence": table["event_sequence"]}
        assert client.post(path, json={"action": "call"}).status_code == 422
        saved = client.post(path, json=body).json()
        assert saved["event_sequence"] == 3 and saved["actor"] == 1 and saved["board"] == []
        assert client.post(path, json=body).status_code == 409
        after, _ = stream_step(client, saved)
        assert after["stage"] == "preflop" and after["board"] == [] and after["actor"] is None
        assert len(app.state.llm.views) == 1
        assert (
            client.post(
                f"/api/poker/sessions/{table['id']}/steps", json={"expected_sequence": 3}
            ).status_code
            == 409
        )
        flop, events = stream_step(client, after)
        assert [event["type"] for event in events] == ["state", "done"]
        assert flop["stage"] == "flop" and len(flop["board"]) == 3 and flop["needs_step"]
        assert len(app.state.llm.views) == 1
        ready, _ = stream_step(client, flop)
        assert ready["actor"] == 0 and not ready["needs_step"] and ready["legal_actions"]


def test_all_in_runout_reveals_one_street_at_a_time_then_showdown(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        table = client.post(
            "/api/poker/sessions?progressive=true", json={"seats": 2, "stack": 100}
        ).json()
        table = client.post(
            f"/api/poker/sessions/{table['id']}/actions?progressive=true",
            json={"action": "all_in", "expected_sequence": table["event_sequence"]},
        ).json()
        stages = []
        while table["needs_step"]:
            table, _ = stream_step(client, table)
            stages.append((table["stage"], len(table["board"])))
            assert_private(table)
        assert stages == [("preflop", 0), ("flop", 3), ("turn", 4), ("river", 5), ("showdown", 5)]
        assert table["completed"] and sum(seat["stack"] for seat in table["seats"]) == 200
        assert all(len(seat["hole"]) == 2 for seat in table["seats"])
        sequences = [event["sequence"] for event in table["event_log"]]
        assert sequences == list(range(1, table["event_sequence"] + 1))
        assert all(event["created_at"] for event in table["event_log"])
        log_before = table["event_log"]
        # If both players are funded, subsequent hands retain global sequence and past action log.
        if all(seat["stack"] for seat in table["seats"]):
            advanced = client.post(
                f"/api/poker/sessions/{table['id']}/next-hand?progressive=true&expected_sequence={table['event_sequence']}",
            ).json()
            assert advanced["event_log"][: len(log_before)] == log_before
            assert advanced["event_sequence"] == table["event_sequence"] + 2


def test_restart_resumes_saved_actor_without_replaying_committed_human_action(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        table = client.post("/api/poker/sessions?progressive=true", json={"seats": 2}).json()
        saved = client.post(
            f"/api/poker/sessions/{table['id']}/actions?progressive=true",
            json={"action": "call", "expected_sequence": table["event_sequence"]},
        ).json()
    reopened = make_app(tmp_path)
    with TestClient(reopened) as client:
        assert client.get(f"/api/poker/sessions/{table['id']}").json() == saved
        after, _ = stream_step(client, saved)
        assert after["event_sequence"] == saved["event_sequence"] + 1
        assert len(reopened.state.llm.views) == 1
        assert (
            len(
                [
                    event
                    for event in after["event_log"]
                    if event["seat"] == 0 and event["action"] == "call"
                ]
            )
            == 1
        )


@pytest.mark.asyncio
async def test_cancel_inference_keeps_last_commit_and_blocks_duplicate_concurrent_steps(tmp_path):
    class BlockingLLM(PokerLLM):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def structured(self, *args, **kwargs):
            self.started.set()
            await self.release.wait()
            return await super().structured(*args, **kwargs)

    llm = BlockingLLM()
    app = make_app(tmp_path, llm)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            table = (
                await client.post("/api/poker/sessions?progressive=true", json={"seats": 2})
            ).json()
            identity = table["id"]
            table = (
                await client.post(
                    f"/api/poker/sessions/{identity}/actions?progressive=true",
                    json={"action": "call", "expected_sequence": table["event_sequence"]},
                )
            ).json()
            path = f"/api/poker/sessions/{identity}/steps"
            body = {"expected_sequence": table["event_sequence"]}
            first = asyncio.create_task(client.post(path, json=body))
            await asyncio.wait_for(llm.started.wait(), 1)
            in_flight = (await client.get(f"/api/poker/sessions/{identity}")).json()
            assert in_flight["phase"] == "choosing"
            assert_private(in_flight)
            assert (await client.post(path, json=body)).status_code == 409
            assert (
                await client.post(
                    f"/api/poker/sessions/{identity}/actions?progressive=true",
                    json={"action": "call", **body},
                )
            ).status_code == 409
            first.cancel()
            await asyncio.gather(first, return_exceptions=True)
            assert (await client.get(f"/api/poker/sessions/{identity}")).json() == table
            assert not app.state.model_queue.lock.locked()
            llm.release.set()
            resumed = await client.post(path, json=body)
            assert resumed.status_code == 200
            assert len(llm.views) == 1
        with app.state.database.session() as session:
            observations = list(
                session.scalars(select(AgentRun).where(AgentRun.route == "poker_bot"))
            )
            assert {row.status for row in observations} == {"interrupted", "complete"}
            assert all("hole" not in json.dumps(row.evidence) for row in observations)
    finally:
        await app.state.integration_services.close()
        app.state.database.engine.dispose()


def test_public_snapshot_does_not_mutate_when_engine_advances():
    state = new_game(2)
    before = public_view(state)
    act(state, "call")
    assert before["event_sequence"] == 2 and len(before["event_log"]) == 2
    assert len(before["history"]) == 2 and before["seats"][0]["bet"] == 5


@pytest.mark.asyncio
async def test_bot_provider_measurements_are_content_free_and_context_is_reset(tmp_path):
    class MeasuredLLM(PokerLLM):
        async def structured(self, model, *args, **kwargs):
            observe_provider(
                {
                    "eval_count": 4,
                    "prompt_eval_count": 120,
                    "eval_duration": 50000000,
                    "total_duration": 60000000,
                    "done_reason": "stop",
                    "thinking": "never record",
                },
                "structured",
                model,
            )
            return await super().structured(model, *args, **kwargs)

    app = make_app(tmp_path, MeasuredLLM())
    state = new_game(2)
    act(state, "call")
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "app": app})
    enclosing: list[dict] = []
    token = provider_observations.set(enclosing)
    try:
        await run_bots(request, state, max_actions=1, defer_settle=True)
        assert provider_observations.get() is enclosing and enclosing == []
        with app.state.database.session() as session:
            row = session.scalar(select(AgentRun).where(AgentRun.route == "poker_bot"))
            metrics = row.evidence["metrics"]
            assert metrics["bot_actions"] == 1 and metrics["fallback_count"] == 0
            calls = metrics["provider_calls"]
            assert len(calls) == 1 and calls[0]["eval_count"] == 4
            assert calls[0]["tokens_per_second"] == 80
            assert "never record" not in json.dumps(metrics)
            assert all("content" not in call and "thinking" not in call for call in calls)
    finally:
        provider_observations.reset(token)
        await app.state.integration_services.close()
        app.state.database.engine.dispose()
