"""Synthetic strategy gates catch legal poor risk and disclose repaired/fallback play."""

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from pixel_station import poker_evaluations as evaluation
from pixel_station.config import AppSettings
from pixel_station.observability import observe_provider, provider_observations
from pixel_station.poker import BotChoice, BotDecision


def scenario(identifier):
    return next(row for row in evaluation.fixtures()["scenarios"] if row["id"] == identifier)


def test_synthetic_fixtures_have_only_current_known_cards_and_engine_legal_examples():
    document = evaluation.fixtures()
    assert len(document["scenarios"]) == 6
    assert "synthetic" in document["description"].lower()
    identity = evaluation.fixture_identity()
    assert identity["version"] == document["version"] and len(identity["sha256"]) == 64
    for row in document["scenarios"]:
        before = deepcopy(row)
        view = evaluation.scenario_view(row)
        assert "deck" not in view and row == before
        assert view["board"] == row["board"]
        assert all(not seat["hole"] for seat in view["seats"] if seat["index"] != view["actor"])
        for choice in row["admissible_examples"]:
            assessed = evaluation.assess_choice(view, choice, row["criteria"])
            assert assessed["final_legal"] and assessed["final_risk_acceptable"]
        bad_risk = evaluation.assess_choice(view, row["rejected_legal_example"], row["criteria"])
        assert bad_risk["final_legal"] and not bad_risk["final_risk_acceptable"]


def test_legality_is_distinct_from_risk_and_raises_are_total_round_bets():
    weak = scenario("weak_deep_open_pressure")
    view = evaluation.scenario_view(weak)
    jam = evaluation.assess_choice(view, BotChoice(action="all_in"), weak["criteria"])
    assert jam["final_legal"] and not jam["final_risk_acceptable"]
    assert jam["chips_committed"] == 990
    premium = scenario("premium_short_vs_raise")
    view = evaluation.scenario_view(premium)
    raise_ = evaluation.assess_choice(
        view, BotChoice(action="raise", amount=50), premium["criteria"]
    )
    assert raise_["final_legal"] and raise_["final_risk_acceptable"]
    assert raise_["raise_total"] == 50 and raise_["chips_committed"] == 40
    illegal = evaluation.assess_choice(
        view, BotChoice(action="raise", amount=49), premium["criteria"]
    )
    assert not illegal["final_legal"] and not illegal["final_risk_acceptable"]
    assert not evaluation.assess_choice(view, {"action": "check"}, premium["criteria"])[
        "final_legal"
    ]


def test_always_fold_and_unbounded_value_bets_do_not_pass():
    results = [
        evaluation.assess_choice(evaluation.scenario_view(row), {"action": "fold"}, row["criteria"])
        for row in evaluation.fixtures()["scenarios"]
    ]
    assert sum(result["final_risk_acceptable"] for result in results) == 3
    strong = scenario("river_nut_straight_value")
    view = evaluation.scenario_view(strong)
    check = evaluation.assess_choice(view, {"action": "check"}, strong["criteria"])
    assert check["final_legal"] and not check["final_risk_acceptable"]
    too_large = evaluation.assess_choice(
        view, {"action": "raise", "amount": 600}, strong["criteria"]
    )
    assert too_large["final_legal"] and not too_large["final_risk_acceptable"]


def test_deterministic_gates_measure_actual_strategy_context_without_inference(monkeypatch):
    cases = evaluation.deterministic_poker_cases()
    assert len(cases) == 6 and {row["status"] for row in cases} == {"PASS"}
    assert all(row["measurements"]["simulation_samples"] == 128 for row in cases)
    assert all(row["measurements"]["engine_chips_conserved"] for row in cases)
    original = evaluation.strategy_context

    def broken(view):
        context = original(view)
        context["observed"]["to_call"] += 1
        return context

    monkeypatch.setattr(evaluation, "strategy_context", broken)
    failed = evaluation.deterministic_poker_cases()
    assert {row["status"] for row in failed} == {"FAIL"}
    assert all(not row["measurements"]["context_checks"]["to_call"] for row in failed)


class ScenarioLLM:
    def __init__(self, mode="reasonable"):
        self.mode = mode
        self.views = []
        self.calls = 0

    async def structured(self, model, messages, schema, **kwargs):
        self.calls += 1
        view = json.loads(messages[-1]["content"].split("\nPrevious action failed", 1)[0])
        self.views.append(view)
        assert schema is BotChoice and kwargs["validation_retries"] == 0
        assert kwargs["options"]["num_predict"] == 180
        assert "decision_context" in view
        observe_provider(
            {
                "eval_count": 12,
                "prompt_eval_count": 200,
                "eval_duration": 100_000_000,
                "done_reason": "stop",
                "thinking": "PRIVATE CHAIN",
                "message": {"content": "PRIVATE CHAIN"},
            },
            "structured",
            model,
        )
        if self.mode == "fold":
            return schema(action="fold", decision_basis="fold")
        if self.mode == "jam":
            return schema(action="all_in", decision_basis="value")
        if self.mode == "repair" and "\nPrevious action failed" not in messages[-1]["content"]:
            return schema(action="all_in", decision_basis="value")
        hero = view["seats"][view["actor"]]
        ranks = sorted(card[0] for card in hero["hole"])
        if view["stage"] == "river" and ranks == ["A", "K"]:
            return schema(action="raise", amount=250, decision_basis="value")
        if ranks == ["K", "K"]:
            return schema(action="all_in", decision_basis="value")
        if ranks == ["A", "A"]:
            return schema(action="raise", amount=30, decision_basis="value")
        return schema(action="fold", decision_basis="fold")


def fake_app(llm):
    settings = AppSettings()
    settings.roles["primary_chat"] = "synthetic-local-model"
    return SimpleNamespace(
        state=SimpleNamespace(llm=llm, model_queue=SimpleNamespace(lock=asyncio.Lock()))
    ), settings


async def test_native_gates_use_production_decision_and_terminal_counters_without_narrative():
    llm = ScenarioLLM()
    app, settings = fake_app(llm)
    cases = await evaluation.native_poker_cases(app, settings)
    assert {row["status"] for row in cases} == {"PASS"}, cases
    assert llm.calls == 6
    assert all(row["measurements"]["provider_call_attempts"] == 1 for row in cases)
    assert all(row["measurements"]["terminal_metadata_samples"] == 1 for row in cases)
    assert all(row["provider_calls"][0]["eval_count"] == 12 for row in cases)
    assert "PRIVATE CHAIN" not in json.dumps(cases)
    assert "reason_short" not in json.dumps(cases) and "private_output" not in json.dumps(cases)
    for view in llm.views:
        assert "deck" not in view
        assert all(not seat["hole"] for seat in view["seats"] if seat["index"] != view["actor"])
    assert provider_observations.get() is None and not app.state.model_queue.lock.locked()


async def test_native_always_fold_fails_premium_and_value_cases_despite_legal_play():
    app, settings = fake_app(ScenarioLLM(mode="fold"))
    cases = await evaluation.native_poker_cases(app, settings)
    assert sum(row["status"] == "FAIL" for row in cases) == 3
    assert all(row["measurements"]["final_legal"] for row in cases)
    free_check = next(row for row in cases if row["id"] == "poker_native_river_nut_straight_value")
    assert free_check["measurements"]["fallback_count"] == 1
    assert free_check["measurements"]["policy_rejections"] == ["fold_with_free_check"] * 2
    assert all(
        row["measurements"]["fallback_count"] == 0 for row in cases if row is not free_check
    )


async def test_native_repaired_risk_and_fallback_are_not_reported_as_native_pass():
    llm = ScenarioLLM(mode="jam")
    app, settings = fake_app(llm)
    cases = await evaluation.native_poker_cases(app, settings)
    weak = next(row for row in cases if row["id"] == "poker_native_weak_deep_open_pressure")
    assert weak["status"] == "FAIL"
    assert weak["measurements"]["final_legal"]
    assert weak["measurements"]["final_risk_acceptable"]
    assert weak["measurements"]["fallback_count"] == 1
    assert weak["measurements"]["repair_attempts"] == 1
    assert weak["measurements"]["provider_call_attempts"] == 2
    assert weak["measurements"]["terminal_metadata_samples"] == 2
    assert weak["measurements"]["validation_rejections"] == 2
    assert weak["measurements"]["raw_actions"] == ["all_in", "all_in"]
    assert len(weak["measurements"]["policy_rejections"]) == 2
    assert weak["measurements"]["decision_basis"] in {"fold", "price"}
    assert weak["measurements"]["strategy_measurements"]["samples"] == 128
    assert llm.calls <= 12 and provider_observations.get() is None


async def test_successful_repair_keeps_the_first_rejected_jam_and_numeric_evidence():
    app, settings = fake_app(ScenarioLLM(mode="repair"))
    cases = await evaluation.native_poker_cases(app, settings)
    weak = next(row for row in cases if row["id"] == "poker_native_weak_deep_open_pressure")
    measured = weak["measurements"]
    assert weak["status"] == "PASS" and measured["fallback_count"] == 0
    assert measured["repair_attempts"] == measured["validation_rejections"] == 1
    assert measured["raw_actions"] == ["all_in", "fold"]
    assert measured["policy_rejections"] == ["preflop_commitment_without_strong_hand"]
    assert measured["decision_basis"] == "fold"
    assert measured["provider_call_attempts"] == measured["terminal_metadata_samples"] == 2
    assert measured["strategy_measurements"]["samples"] == 128
    assert 0 <= measured["strategy_measurements"]["equity_share_estimate"] <= 1
    assert measured["strategy_measurements"]["pot_odds"] == pytest.approx(20 / 95, abs=0.0001)


def test_decision_measurements_keep_only_bounded_codes_and_numeric_strategy_evidence():
    measured = evaluation.decision_measurements(
        {
            "validation_rejections": 1,
            "raw_actions": ["all_in", "fold", "PRIVATE NARRATIVE"],
            "policy_rejections": ["raise_above_risk_budget", "PRIVATE NARRATIVE"],
            "decision_basis": "PRIVATE NARRATIVE",
            "strategy_measurements": {
                "samples": 128,
                "equity_share_estimate": 0.4,
                "pot_odds": 0.2,
                "reason": "PRIVATE NARRATIVE",
                "thinking": "PRIVATE NARRATIVE",
            },
            "reason_short": "PRIVATE NARRATIVE",
        }
    )
    assert "PRIVATE" not in json.dumps(measured)
    assert measured["raw_actions"] == ["all_in", "fold"]
    assert measured["policy_rejections"] == ["raise_above_risk_budget"]
    assert measured["decision_basis"] is None
    assert measured["strategy_measurements"] == {
        "samples": 128,
        "equity_share_estimate": 0.4,
        "pot_odds": 0.2,
    }


async def test_native_missing_model_skips_without_decisions(monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("No model must not execute a native decision")

    monkeypatch.setattr(evaluation, "choose_bot_action", forbidden)
    app, settings = fake_app(ScenarioLLM())
    settings.roles["primary_chat"] = ""
    cases = await evaluation.native_poker_cases(app, settings)
    assert len(cases) == 6 and {row["status"] for row in cases} == {"SKIP"}
    assert not app.state.llm.calls


async def test_combined_deadline_bounds_attempts_and_context_resets(monkeypatch):
    calls = 0

    async def slow(*args, **kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(1)

    monkeypatch.setattr(evaluation, "NATIVE_POKER_BUDGET", 0.02)
    monkeypatch.setattr(evaluation, "choose_bot_action", slow)
    app, settings = fake_app(ScenarioLLM())
    async with asyncio.timeout(0.5):
        cases = await evaluation.native_poker_cases(app, settings)
    assert calls == 1 and {row["status"] for row in cases} == {"ERROR"}
    assert all(row["error_type"] == "TimeoutError" for row in cases)
    assert provider_observations.get() is None


async def test_cancellation_propagates_and_resets_parent_observer(monkeypatch):
    entered = asyncio.Event()
    parent = []

    async def wait(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(evaluation, "choose_bot_action", wait)
    token = provider_observations.set(parent)
    try:
        app, settings = fake_app(ScenarioLLM())
        task = asyncio.create_task(evaluation.native_poker_cases(app, settings))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert provider_observations.get() is parent
    finally:
        provider_observations.reset(token)


async def test_mutating_decision_input_fails_instead_of_concealing_side_effect(monkeypatch):
    async def mutate(app, view, settings, **kwargs):
        view["history"].append({"action": "unexpected_mutation"})
        return BotDecision(choice=BotChoice(action="fold"), metrics={"fallback_count": 0})

    monkeypatch.setattr(evaluation, "choose_bot_action", mutate)
    app, settings = fake_app(ScenarioLLM())
    cases = await evaluation.native_poker_cases(app, settings)
    assert {row["status"] for row in cases} == {"FAIL"}
    assert all(not row["measurements"]["input_unchanged"] for row in cases)
