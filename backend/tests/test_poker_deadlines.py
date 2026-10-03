import asyncio
import random
import threading
from types import SimpleNamespace

import pytest

from pixel_station.config import AppSettings
from pixel_station.observability import provider_observations
from pixel_station.poker import (
    BotChoice,
    bot_decision_view,
    choose_bot_action,
    fallback_bot_choice,
    new_game,
    public_view,
    validate_bot_choice,
)
from pixel_station.poker_evaluations import fixtures, scenario_view
from pixel_station.poker_strategy import strategy_context


class ForbiddenProvider:
    def __init__(self):
        self.calls = 0

    async def structured(self, *args, **kwargs):
        self.calls += 1
        pytest.fail("A decision whose deadline expired must never start inference")


def bounded_app():
    provider = ForbiddenProvider()
    app = SimpleNamespace(
        state=SimpleNamespace(llm=provider, model_queue=SimpleNamespace(lock=asyncio.Lock()))
    )
    settings = AppSettings()
    settings.roles["primary_chat"] = "deadline-test"
    return app, settings, provider


@pytest.mark.asyncio
async def test_context_worker_deadline_returns_disclosed_fallback_without_fabricated_evidence(
    monkeypatch,
):
    import pixel_station.poker_strategy as strategy_module

    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = strategy_module.strategy_context

    def blocked_context(view):
        entered.set()
        try:
            assert release.wait(2), "Test did not release the bounded worker"
            return original(view)
        finally:
            finished.set()

    monkeypatch.setattr(strategy_module, "strategy_context", blocked_context)
    app, settings, provider = bounded_app()
    view = scenario_view(fixtures()["scenarios"][0])
    enclosing: list[dict] = []
    token = provider_observations.set(enclosing)
    task = asyncio.create_task(choose_bot_action(app, view, settings, timeout_seconds=0.05))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        done, _ = await asyncio.wait({task}, timeout=0.3)
        assert task in done, "The context worker wait escaped the whole-decision deadline"
        decision = await task
        assert decision.choice.action in {"check", "fold"}
        assert decision.metrics["fallback_count"] == 1
        assert "ContextTimeout" in decision.metrics["policy_rejections"]
        assert not decision.metrics.get("strategy_measurements")
        assert decision.metrics["provider_call_attempts"] == provider.calls == 0
        assert provider_observations.get() is enclosing and enclosing == []
        assert not release.is_set(), "Fallback incorrectly waited for evidence to finish"
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert await asyncio.to_thread(finished.wait, 1)
        provider_observations.reset(token)


@pytest.mark.asyncio
async def test_queued_callback_deadline_keeps_real_evidence_and_starts_no_inference():
    entered = asyncio.Event()

    async def blocked_phase(phase, actor):
        assert phase == "queued"
        entered.set()
        await asyncio.Event().wait()

    app, settings, provider = bounded_app()
    view = scenario_view(fixtures()["scenarios"][0])
    task = asyncio.create_task(
        choose_bot_action(app, view, settings, on_phase=blocked_phase, timeout_seconds=0.1)
    )
    try:
        await asyncio.wait_for(entered.wait(), 1)
        done, _ = await asyncio.wait({task}, timeout=0.3)
        assert task in done, "The queued callback escaped the whole-decision deadline"
        decision = await task
        assert decision.choice.action in {"check", "call", "fold", "raise"}
        assert decision.metrics["fallback_count"] == 1
        assert "ContextTimeout" not in decision.metrics["policy_rejections"]
        assert decision.metrics["strategy_measurements"]["samples"] == 128
        assert decision.metrics["provider_call_attempts"] == provider.calls == 0
        assert not app.state.model_queue.lock.locked()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def board_only_premium_river(*, facing_bet=False):
    state = new_game(2, rng=random.Random(4))
    state.update(
        stage="river",
        actor=1,
        dealer=1,
        board=["Ks", "Qs", "Js", "Ts", "9s"],
        current_bet=90 if facing_bet else 0,
        last_raise=90 if facing_bet else 10,
        last_acted={},
        pending=[0, 1],
        history=[],
    )
    state["seats"][1].update(
        hole=["Ah", "Ad"], stack=90, bet=0, contribution=0 if facing_bet else 20
    )
    state["seats"][0].update(
        hole=["2h", "3h"],
        stack=900,
        bet=90 if facing_bet else 0,
        contribution=90 if facing_bet else 20,
    )
    return bot_decision_view(public_view(state, 1))


def test_preflop_premium_does_not_authorize_postflop_short_stack_jam_or_fallback_value_raise():
    view = board_only_premium_river()
    context = strategy_context(view)
    assert context["hand"]["preflop_tier"] == "premium"
    assert not context["hand"]["improves_board_hand"]
    assert context["simulation"]["equity_share_estimate"] < 0.72
    assert validate_bot_choice(view, context, BotChoice(action="all_in")) == (
        "stack_commitment_without_value_or_price"
    )
    assert fallback_bot_choice(view, context).action == "check"


def test_preflop_premium_does_not_exempt_postflop_full_stack_call_from_price_guard():
    view = board_only_premium_river(facing_bet=True)
    context = strategy_context(view)
    assert context["hand"]["preflop_tier"] == "premium"
    assert context["observed"]["pot_odds"] == 0.5
    assert context["simulation"]["equity_share_estimate"] < 0.54
    assert validate_bot_choice(view, context, BotChoice(action="call")) == (
        "call_price_exceeds_equity_margin"
    )
    assert fallback_bot_choice(view, context).action == "fold"
