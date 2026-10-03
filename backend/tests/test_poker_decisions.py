import asyncio
import copy
import json
import random

import pytest

from pixel_station.app import create_app
from pixel_station.config import AppSettings
from pixel_station.observability import provider_observations
from pixel_station.poker import (
    bot_decision_view,
    choose_bot_action,
    legal_actions,
    new_game,
    public_view,
)


def pressure_view(cards=("7s", "2d"), bet=30):
    state = new_game(2, 1000, rng=random.Random(5))
    state.update(
        actor=1, current_bet=bet, last_raise=max(10, bet - 10), last_acted={}, pending=[0, 1]
    )
    state["seats"][0].update(stack=1000 - bet, bet=bet, contribution=bet)
    state["seats"][1].update(stack=1000, bet=0, contribution=0, hole=list(cards))
    assert legal_actions(state)
    return public_view(state, 1)


class JamLLM:
    def __init__(self):
        self.calls = []

    async def structured(self, model, messages, schema, **kwargs):
        self.calls.append((model, messages, kwargs))
        return schema(action="all_in", decision_basis="value")


def decision_app(tmp_path, llm):
    app = create_app(tmp_path, llm=llm, discover=False)
    settings = AppSettings(harness_enabled=False)
    settings.roles["primary_chat"] = "poker-test"
    app.state.set_settings(settings)
    return app, settings


@pytest.mark.asyncio
async def test_repeated_weak_deep_jams_are_rejected_and_fallback_does_not_call(tmp_path):
    llm = JamLLM()
    app, settings = decision_app(tmp_path, llm)
    view = pressure_view()
    original = copy.deepcopy(view)
    result = await choose_bot_action(app, view, settings)
    assert result.choice.action == "fold"
    assert result.metrics["raw_actions"] == ["all_in", "all_in"]
    assert result.metrics["validation_rejections"] == 2
    assert result.metrics["repair_count"] == 1
    assert result.metrics["fallback_count"] == 1
    assert view == original  # Decision helper is the same pure path used by native gates.
    assert provider_observations.get() is None


@pytest.mark.asyncio
async def test_premium_deep_open_jam_is_replaced_with_bounded_value_play(tmp_path):
    app, settings = decision_app(tmp_path, JamLLM())
    result = await choose_bot_action(app, pressure_view(("As", "Ad"), bet=10), settings)
    assert result.choice.action == "raise"
    assert 20 <= result.choice.amount <= 100
    assert result.metrics["fallback_count"] == 1
    assert result.metrics["policy_rejections"] == ["stack_commitment_without_value_or_price"] * 2


@pytest.mark.asyncio
async def test_actual_prompt_has_only_actor_cards_and_bounded_public_facts(tmp_path):
    llm = JamLLM()
    app, settings = decision_app(tmp_path, llm)
    view = pressure_view()
    view["deck"] = ["PRIVATE_DECK_SENTINEL"]
    view["seats"][0]["hole"] = ["Ah", "Ac"]
    view["seats"][0]["personality"] = "aggressive"
    view["event_log"] = [{"private": "PRIVATE_EVENT_SENTINEL"}]
    await choose_bot_action(app, view, settings)
    content = llm.calls[0][1][1]["content"]
    sent = json.loads(content)
    assert sent["seats"][0]["hole"] == []
    assert sent["seats"][1]["hole"] == ["7s", "2d"]
    assert "PRIVATE" not in content and "aggressive" not in content
    assert (
        "decision_context" in sent
        and "equity_share_estimate" in sent["decision_context"]["simulation"]
    )
    assert llm.calls[0][2]["options"]["num_predict"] == 180
    assert llm.calls[0][2]["options"]["temperature"] == 0.35
    assert "reason_short" not in llm.calls[0][1][0]["content"]
    assert bot_decision_view(view)["style"] == "value_focused"


@pytest.mark.asyncio
async def test_no_model_uses_price_aware_fallback_without_provider_calls(tmp_path):
    llm = JamLLM()
    app, settings = decision_app(tmp_path, llm)
    settings.roles["primary_chat"] = ""
    result = await choose_bot_action(app, pressure_view(bet=300), settings)
    assert result.choice.action == "fold" and result.metrics["fallback_count"] == 1
    assert result.metrics["provider_call_attempts"] == 0 and not llm.calls


@pytest.mark.asyncio
async def test_free_check_cannot_be_replaced_by_repeated_model_folds(tmp_path):
    class AlwaysFold:
        async def structured(self, model, messages, schema, **kwargs):
            return schema(action="fold", decision_basis="fold")

    app, settings = decision_app(tmp_path, AlwaysFold())
    view = pressure_view(("7s", "2d"), bet=10)
    view["seats"][1].update(bet=10, stack=990, contribution=10)
    view["pot"] = 20
    view["legal_actions"] = [
        {"action": "fold"},
        {"action": "check"},
        {"action": "raise", "min": 20, "max": 1000},
        {"action": "all_in", "amount": 990},
    ]
    result = await choose_bot_action(app, view, settings)
    assert result.choice.action == "check"
    assert result.metrics["policy_rejections"] == ["fold_with_free_check"] * 2
    assert result.metrics["fallback_count"] == 1


@pytest.mark.asyncio
async def test_cancelled_decision_releases_queue_and_observer_without_fallback(tmp_path):
    entered = asyncio.Event()

    class Blocked:
        async def structured(self, *args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

    app, settings = decision_app(tmp_path, Blocked())
    task = asyncio.create_task(choose_bot_action(app, pressure_view(), settings))
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not app.state.model_queue.lock.locked()
    assert provider_observations.get() is None
