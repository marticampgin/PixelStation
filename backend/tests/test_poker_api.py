"""Poker API persistence and AI isolation regressions; inference doubles stay in tests."""

import asyncio
import json
import random

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.requests import Request

from pixel_station.app import create_app
from pixel_station.config import AppSettings
from pixel_station.database import FrictionEvent
from pixel_station.poker import PokerAction, act, finish, legal_actions, new_game, run_bots


class PokerLLM:
    def __init__(self, invalid=False):
        self.views = []
        self.invalid = invalid

    async def models(self):
        return {"available": True, "models": [{"name": "poker-test", "capabilities": ["completion"]}]}

    async def structured(self, model, messages, schema, **kwargs):
        assert model == "poker-test" and schema.__name__ == "BotChoice"
        content = messages[-1]["content"].split("\nPrevious action failed", 1)[0]
        view = json.loads(content)
        self.views.append(view)
        if self.invalid:
            return schema(action="raise", amount=1)
        options = {option["action"] for option in view["legal_actions"]}
        return schema(action="check" if "check" in options else "call" if "call" in options else "fold")


def make_app(tmp_path, llm=None, model=True):
    app = create_app(tmp_path, llm=llm or PokerLLM(), discover=False)
    settings = AppSettings(harness_enabled=False)
    if model:
        settings.roles["primary_chat"] = "poker-test"
    app.state.set_settings(settings)
    return app


def test_poker_legal_complete_hand_bot_context_isolation_and_persistence(tmp_path):
    llm = PokerLLM()
    app = make_app(tmp_path, llm)
    with TestClient(app) as client:
        response = client.post("/api/poker/sessions", json={"seats": 2, "stack": 1000})
        assert response.status_code == 200, response.text
        table = response.json()
        id_ = table["id"]
        assert len(table["seats"][0]["hole"]) == 2 and table["seats"][1]["hole"] == []
        for _ in range(10):
            if table["completed"]:
                break
            assert table["actor"] == 0
            options = {choice["action"] for choice in table["legal_actions"]}
            action = "check" if "check" in options else "call"
            result = client.post(f"/api/poker/sessions/{id_}/actions", json={"action": action})
            assert result.status_code == 200, result.text
            table = result.json()
        assert table["completed"] and table["stage"] == "showdown"
        assert len(table["board"]) == 5 and sum(seat["stack"] for seat in table["seats"]) == 2000
        assert table["winners"]
        assert llm.views
        for view in llm.views:
            actor = view["actor"]
            assert actor == 1
            assert "deck" not in view and "last_acted" not in view
            assert len(view["seats"][actor]["hole"]) == 2
            assert all(seat["hole"] == [] for seat in view["seats"] if seat["index"] != actor)
        with app.state.database.session() as session:
            saved = list(session.scalars(select(PokerAction).where(PokerAction.session_id == id_)))
            assert len(saved) == len(table["history"])
        assert client.get("/api/poker/sessions").json()[0]["id"] == id_
    reopened = make_app(tmp_path)
    with TestClient(reopened) as client:
        assert client.get(f"/api/poker/sessions/{id_}").json() == table
        next_response = client.post(f"/api/poker/sessions/{id_}/next-hand")
        assert next_response.status_code == 200
        assert next_response.json()["hand_number"] == 2
        assert next_response.json()["dealer"] == 1


def test_illegal_human_api_action_does_not_change_persisted_hand(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        before = client.post("/api/poker/sessions", json={"seats": 2}).json()
        id_ = before["id"]
        assert client.post(f"/api/poker/sessions/{id_}/actions", json={"action": "check"}).status_code == 422
        assert client.post(f"/api/poker/sessions/{id_}/actions", json={"action": "raise", "amount": 1}).status_code == 422
        assert client.post(f"/api/poker/sessions/{id_}/next-hand").status_code == 422
        assert client.get(f"/api/poker/sessions/{id_}").json() == before
        assert client.get("/api/poker/sessions/missing").status_code == 404
        assert client.post("/api/poker/sessions", json={"seats": 2, "small_blind": 10, "big_blind": 5}).status_code == 422


def test_missing_local_model_uses_disclosed_legal_fallback(tmp_path):
    llm = PokerLLM()
    app = make_app(tmp_path, llm, model=False)
    with TestClient(app) as client:
        table = client.post("/api/poker/sessions", json={"seats": 2}).json()
        result = client.post(f"/api/poker/sessions/{table['id']}/actions", json={"action": "call"})
        assert result.status_code == 200
        view = result.json()
        assert "fallback" in view["model_status"]
        assert view["actor"] == 0 and view["stage"] == "flop"
        assert not llm.views


def test_invalid_bot_action_retries_once_and_records_friction(tmp_path):
    llm = PokerLLM(invalid=True)
    app = make_app(tmp_path, llm)
    with TestClient(app) as client:
        table = client.post("/api/poker/sessions", json={"seats": 2}).json()
        result = client.post(f"/api/poker/sessions/{table['id']}/actions", json={"action": "call"})
        assert result.status_code == 200, result.text
        assert result.json()["actor"] == 0 and "fallback" in result.json()["model_status"]
        # One preflop AI action and one flop AI action, each retried exactly once.
        assert len(llm.views) == 4
        with app.state.database.session() as session:
            events = list(session.scalars(select(FrictionEvent).where(FrictionEvent.kind == "invalid_poker_action")))
            assert len(events) == 4
        assert all(item["amount"] != 1 for item in result.json()["history"] if item["action"] == "raise")


@pytest.mark.asyncio
async def test_async_bot_queue_wait_is_inside_bounded_budget(tmp_path, monkeypatch):
    """A busy chat queue must not leave Poker waiting beyond its own timeout."""
    app = make_app(tmp_path)
    state = new_game(2, rng=random.Random(1))
    act(state, "call")
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "app": app})
    await app.state.model_queue.lock.acquire()
    monkeypatch.setattr("pixel_station.poker.BOT_TIME_BUDGET", 0.02)
    task = asyncio.create_task(run_bots(request, state))
    try:
        # Provider deadline is now nearly exhausted. Queue wait must timeout too.
        done, _ = await asyncio.wait({task}, timeout=0.35)
        assert task in done, "Poker deadline excludes model queue waiting"
        assert state["actor"] == 0 or state["completed"]
        assert "fallback" in state["model_status"]
    finally:
        app.state.model_queue.lock.release()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await app.state.integration_services.close()
        app.state.database.engine.dispose()


@pytest.mark.asyncio
async def test_bot_action_budget_finishes_legal_hand_without_losing_human_all_in(tmp_path, monkeypatch):
    class RaisingLLM(PokerLLM):
        async def structured(self, model, messages, schema, **kwargs):
            view = json.loads(messages[-1]["content"])
            self.views.append(view)
            raise_option = next((choice for choice in view["legal_actions"] if choice["action"] == "raise"), None)
            if raise_option:
                return schema(action="raise", amount=raise_option["min"])
            legal = {choice["action"] for choice in view["legal_actions"]}
            return schema(action="check" if "check" in legal else "call")

    llm = RaisingLLM()
    app = make_app(tmp_path, llm)
    state = new_game(3, rng=random.Random(9))
    state["seats"][0]["stack"] = 15
    state["seats"][1]["stack"] = 100_000
    state["seats"][2]["stack"] = 100_000
    state["initial_chips"] = sum(seat["stack"] + seat["contribution"] for seat in state["seats"])
    act(state, "all_in")
    monkeypatch.setattr("pixel_station.poker.BOT_MAX_MODEL_ACTIONS", 3)
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": [], "app": app})
    await run_bots(request, state)
    assert len(llm.views) == 3
    assert state["completed"] and state["stage"] == "showdown"
    assert sum(seat["stack"] for seat in state["seats"]) == state["initial_chips"]
    assert state["history"][2]["seat"] == 0 and state["history"][2]["action"] == "all_in"
    assert "fallback" in state["model_status"]
    await app.state.integration_services.close()
    app.state.database.engine.dispose()


def test_short_opening_all_in_does_not_use_limit_poker_completion():
    # TDA/RRoP No-Limit ¶2: a short opening all-in needs a full BB increment to raise.
    state = new_game(3, rng=random.Random(2))
    state.update(stage="flop", board=["2c", "3d", "4s"], actor=0, current_bet=0,
                 last_raise=10, last_acted={}, pending=[0, 1, 2])
    for seat in state["seats"]:
        seat["bet"] = 0
    state["seats"][0]["stack"] = 6
    act(state, "all_in")
    raise_action = next(choice for choice in legal_actions(state) if choice["action"] == "raise")
    assert raise_action["min"] == 16


def test_split_pot_odd_chip_goes_left_of_button_and_excess_is_returned():
    state = new_game(3, rng=random.Random(3))
    state.update(board="As Ks Qs Js Ts".split(), dealer=0, initial_chips=35)
    for seat, hole, contribution, folded in zip(state["seats"], ["2d 3d", "4d 5d", "6d 7d"], [10, 20, 5], [False, False, True], strict=True):
        seat.update(hole=hole.split(), contribution=contribution, stack=0, folded=folded)
    finish(state)
    # 15-chip main pot ties: seat 1 gets odd chip. 10-chip side pot ties. 10 excess to seat 1.
    assert [seat["stack"] for seat in state["seats"]] == [12, 23, 0]
