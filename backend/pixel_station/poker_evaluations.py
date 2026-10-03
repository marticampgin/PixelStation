"""Public synthetic Poker risk gates using the same decision path as real tables.

These fixtures check obvious risk/value constraints, not optimal expected value.
No opponent cards, future cards, private user hands, or model narratives are saved.
"""

import asyncio
import hashlib
import json
import math
import re
import time
from copy import deepcopy
from pathlib import Path

from .observability import provider_observations
from .poker import act, bot_decision_view, public_view

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "poker_evaluations_v1.json"
NATIVE_POKER_BUDGET = 90.0
NATIVE_SCENARIO_BUDGET = 15.0


def fixtures() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def fixture_identity() -> dict:
    return {
        "version": fixtures()["version"],
        "sha256": hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest(),
    }


def poker_fixture_identity() -> dict:
    return {**fixture_identity(), "policy_version": policy_version()}


def strategy_context(view: dict) -> dict:
    from .poker_strategy import strategy_context as calculate_context

    return calculate_context(view)


def policy_version():
    from .poker_strategy import STRATEGY_VERSION

    return STRATEGY_VERSION


async def choose_bot_action(app, view, settings, *, timeout_seconds):
    from .poker import choose_bot_action as production_decision

    return await production_decision(app, view, settings, timeout_seconds=timeout_seconds)


def scenario_state(scenario: dict) -> dict:
    """Construct only current public facts and the acting seat's synthetic cards."""
    actor = scenario["actor"]
    if not 0 <= actor < len(scenario["seats"]):
        raise ValueError("Fixture actor is outside the table")
    cards = scenario["hole"] + scenario["board"]
    if len(scenario["hole"]) != 2 or len(set(cards)) != len(cards):
        raise ValueError("Fixture has invalid or repeated known cards")
    if (
        len(scenario["board"])
        != {"preflop": 0, "flop": 3, "turn": 4, "river": 5}[scenario["stage"]]
    ):
        raise ValueError("Fixture board does not match its street")
    seats = [
        {
            "index": index,
            "name": f"Synthetic seat {index}",
            "personality": "balanced",
            "hole": list(scenario["hole"]) if index == actor else [],
            "stack": row["stack"],
            "bet": row["bet"],
            "contribution": row["contribution"],
            "folded": row.get("folded", False),
            "all_in": row.get("all_in", row["stack"] == 0),
        }
        for index, row in enumerate(scenario["seats"])
    ]
    return {
        "id": f"synthetic:{scenario['id']}",
        "hand_number": 1,
        "stage": scenario["stage"],
        "actor": actor,
        "dealer": scenario["dealer"],
        "board": list(scenario["board"]),
        "seats": seats,
        "small_blind": 5,
        "big_blind": 10,
        "current_bet": scenario["current_bet"],
        "last_raise": scenario["last_raise"],
        "last_acted": {},
        "pending": [row["index"] for row in seats if not row["folded"] and not row["all_in"]],
        "completed": False,
        "winners": [],
        "model_status": "",
        "history": [{**row, "stage": scenario["stage"]} for row in scenario["history"]],
        "initial_chips": sum(row["stack"] + row["contribution"] for row in seats),
        "event_sequence": 0,
        "event_log": [],
    }


def scenario_view(scenario: dict) -> dict:
    state = scenario_state(scenario)
    return bot_decision_view(public_view(state, scenario["actor"]))


def assess_choice(view: dict, choice, criteria: dict) -> dict:
    """Score legality independently from this scenario's public risk/value constraints."""
    if choice is None:
        return {"final_legal": False, "final_risk_acceptable": False, "action": None}
    action = choice.get("action") if isinstance(choice, dict) else choice.action
    amount = choice.get("amount") if isinstance(choice, dict) else choice.amount
    options = {row["action"]: row for row in view["legal_actions"]}
    option = options.get(action)
    legal = option is not None
    paid = 0
    if option is not None:
        if action == "raise":
            legal = (
                isinstance(amount, int)
                and not isinstance(amount, bool)
                and option["min"] <= amount <= option["max"]
            )
            paid = amount - view["seats"][view["actor"]]["bet"] if legal else 0
        elif action in {"call", "all_in"}:
            paid = option["amount"]
    risk = legal and action in criteria["allowed_actions"]
    if risk and action == "raise":
        assert option is not None and isinstance(amount, int)
        risk = (
            criteria.get("raise_total_min", option["min"])
            <= amount
            <= criteria.get("raise_total_max", option["max"])
        )
    if risk and "max_paid" in criteria:
        risk = paid <= criteria["max_paid"]
    return {
        "final_legal": legal,
        "final_risk_acceptable": risk,
        "action": action,
        "raise_total": amount if action == "raise" and legal else None,
        "chips_committed": paid if legal else None,
    }


def context_measurements(scenario: dict, view: dict, context: dict) -> dict:
    observed, hand = context["observed"], context["hand"]
    simulation, guidance = context["simulation"], context["guidance"]
    expected = scenario["expected_context"]
    checks = {
        "to_call": observed["to_call"] == expected["to_call"],
        "hero_stack_bb": observed["hero_stack_bb"] == expected["hero_stack_bb"],
        "pot": observed["pot"] == view["pot"],
        "bounded_simulation": simulation["samples"] == 128,
        "finite_equity_share": isinstance(simulation["equity_share_estimate"], (int, float))
        and 0 <= simulation["equity_share_estimate"] <= 1,
        "no_hidden_opponent_cards": all(
            not row["hole"] for row in view["seats"] if row["index"] != view["actor"]
        ),
    }
    if "preflop_tier" in expected:
        checks["preflop_tier"] = hand["preflop_tier"] == expected["preflop_tier"]
        checks["weak_deep_jam_discouraged"] = (
            guidance["weak_deep_jam_discouraged"] == expected["weak_deep_jam_discouraged"]
        )
    if "made_category" in expected:
        checks["made_category"] = hand["made_category"] == expected["made_category"]
    return {
        "context_checks": checks,
        "to_call": observed["to_call"],
        "hero_stack_bb": observed["hero_stack_bb"],
        "simulation_samples": simulation["samples"],
        "equity_share_estimate": simulation["equity_share_estimate"],
    }


def deterministic_poker_cases() -> list[dict]:
    """Check actual tactical evidence and engine risk gates without native inference."""
    document = fixtures()
    cases = []
    for scenario in document["scenarios"]:
        started = time.perf_counter()
        case = {
            "id": f"poker_context_{scenario['id']}",
            "label": scenario["label"],
            "scope": "isolated_fixture",
            "critical": True,
            "strategy_fixture_version": document["version"],
        }
        try:
            view = scenario_view(scenario)
            before = deepcopy(view)
            measured = context_measurements(scenario, view, strategy_context(view))
            examples = [
                assess_choice(view, choice, scenario["criteria"])
                for choice in scenario["admissible_examples"]
            ]
            rejected = assess_choice(view, scenario["rejected_legal_example"], scenario["criteria"])
            state = scenario_state(scenario)
            original_chips = sum(row["stack"] + row["contribution"] for row in state["seats"])
            choice = scenario["admissible_examples"][0]
            act(state, choice["action"], choice.get("amount"), settle=False)
            conserved = (
                sum(row["stack"] + row["contribution"] for row in state["seats"]) == original_chips
            )
            measured.update(
                {
                    "admissible_examples_legal_and_risk_acceptable": all(
                        row["final_legal"] and row["final_risk_acceptable"] for row in examples
                    ),
                    "bad_risk_example_is_legal": rejected["final_legal"],
                    "bad_risk_example_rejected": not rejected["final_risk_acceptable"],
                    "engine_chips_conserved": conserved,
                    "context_input_unchanged": view == before,
                    "future_cards_absent": state["board"] == scenario["board"]
                    and "deck" not in state,
                }
            )
            passed = all(measured["context_checks"].values()) and all(
                value
                for key, value in measured.items()
                if key
                in {
                    "admissible_examples_legal_and_risk_acceptable",
                    "bad_risk_example_is_legal",
                    "bad_risk_example_rejected",
                    "engine_chips_conserved",
                    "context_input_unchanged",
                    "future_cards_absent",
                }
            )
            case.update(
                status="PASS" if passed else "FAIL",
                policy_version=policy_version(),
                measurements=measured,
            )
        except Exception as exc:
            case.update(status="ERROR", error_type=type(exc).__name__)
        case["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        cases.append(case)
    return cases


def terminal_metadata(events: list[dict]) -> list[dict]:
    """Copy only native counters and fixed observer metadata; never model content."""
    fields = {
        "operation",
        "model",
        "result_status",
        "done_reason",
        "measurement_source",
        "prompt_eval_count",
        "eval_count",
        "total_duration",
        "load_duration",
        "prompt_eval_duration",
        "eval_duration",
        "tokens_per_second",
    }
    return [{key: value for key, value in row.items() if key in fields} for row in events]


def skipped_poker_cases(reason: str | None = None) -> list[dict]:
    document = fixtures()
    return [
        {
            "id": f"poker_native_{scenario['id']}",
            "label": scenario["label"],
            "scope": "native_model",
            "critical": True,
            "strategy_fixture_version": document["version"],
            "policy_version": policy_version(),
            "status": "SKIP",
            "reason": reason
            or "Native Poker probes were not requested; synthetic deterministic gates do not validate model judgment.",
        }
        for scenario in document["scenarios"]
    ]


def decision_measurements(metrics: dict) -> dict:
    """Preserve bounded coded decisions and numeric evidence, never model narratives."""
    actions = {"fold", "check", "call", "raise", "all_in"}
    bases = {"value", "price", "free_check", "fold", "small_bluff"}
    strategy = {}
    source = metrics.get("strategy_measurements", {})
    if isinstance(source, dict):
        for key in ("equity_share_estimate", "samples", "pot_odds"):
            value = source.get(key)
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                and 0 <= value <= (128 if key == "samples" else 1)
            ):
                strategy[key] = value
    return {
        "validation_rejections": metrics.get("validation_rejections", 0),
        "raw_actions": [
            action for action in metrics.get("raw_actions", [])[:2] if action in actions
        ],
        "policy_rejections": [
            code
            for code in metrics.get("policy_rejections", [])[:2]
            if isinstance(code, str) and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,79}", code)
        ],
        "decision_basis": metrics.get("decision_basis")
        if metrics.get("decision_basis") in bases
        else None,
        "strategy_measurements": strategy,
    }


async def native_poker_cases(app, settings) -> list[dict]:
    """Explicit-only, <=90s combined; six <=15s production decisions including retry.

    A legal deterministic fallback is reported but fails native judgment validation.
    The caller owns the separate opt-in evaluation scope.
    """
    document = fixtures()
    model = settings.roles.get("primary_chat", "")
    if not model:
        return skipped_poker_cases("No primary local model assigned")
    deadline = time.perf_counter() + NATIVE_POKER_BUDGET
    from .evaluations import native_runtime_identity

    runtime = {
        "ollama": "not_available",
        "model": model,
        "digest": None,
        "provider": type(app.state.llm).__name__,
    }
    combined_exhausted = False
    remaining = deadline - time.perf_counter()
    identity_constrained_by_combined = remaining <= 3.0
    identity_scope = asyncio.timeout(max(0, min(3.0, remaining)))
    try:
        async with identity_scope:
            runtime = await native_runtime_identity(app, settings)
    except Exception:
        # Unknown identity disables comparisons; an ordinary metadata failure does
        # not prevent decisions when the combined inference budget remains.
        combined_exhausted = identity_constrained_by_combined and identity_scope.expired()
    results = []
    for scenario in document["scenarios"]:
        case = {
            "id": f"poker_native_{scenario['id']}",
            "label": scenario["label"],
            "scope": "native_model",
            "critical": True,
            "strategy_fixture_version": document["version"],
            "policy_version": policy_version(),
            "runtime": runtime,
        }
        started = time.perf_counter()
        observations: list[dict] = []
        token = provider_observations.set(observations)
        remaining = 0 if combined_exhausted else deadline - started
        constrained_by_combined = remaining <= NATIVE_SCENARIO_BUDGET
        try:
            timeout = min(NATIVE_SCENARIO_BUDGET, remaining)
            if timeout <= 0:
                raise TimeoutError("Combined Poker probe budget exhausted")
            async with asyncio.timeout(timeout):
                view = scenario_view(scenario)
                before = deepcopy(view)
                decision = await choose_bot_action(app, view, settings, timeout_seconds=timeout)
            measured = assess_choice(view, decision.choice, scenario["criteria"])
            metrics = decision.metrics
            fallback = metrics.get("fallback_count", 0)
            measured.update(decision_measurements(metrics))
            measured.update(
                {
                    "fallback_count": fallback,
                    "repair_attempts": metrics.get("repair_count", 0),
                    "provider_call_attempts": metrics.get("provider_call_attempts"),
                    "model_judgment_without_fallback": decision.choice is not None
                    and fallback == 0,
                    "input_unchanged": view == before,
                    "decision_path": "production_choose_bot_action",
                    "scenario_timeout_seconds": timeout,
                    "combined_budget_seconds": NATIVE_POKER_BUDGET,
                }
            )
            events = observations or metrics.get("provider_calls", [])
            measured["terminal_metadata_samples"] = len(events)
            passed = (
                measured["final_legal"]
                and measured["final_risk_acceptable"]
                and measured["model_judgment_without_fallback"]
                and measured["input_unchanged"]
            )
            case.update(
                status="PASS" if passed else "FAIL",
                model=model,
                policy_version=policy_version(),
                measurements=measured,
                provider_calls=terminal_metadata(events),
            )
        except Exception as exc:
            if isinstance(exc, TimeoutError) and constrained_by_combined:
                # Windows' event-loop clock may deliver its timeout before the
                # high-resolution elapsed clock reaches the same deadline. Once
                # the final allocated slot expires, no new decision may start.
                combined_exhausted = True
            case.update(
                status="ERROR",
                model=model,
                error_type=type(exc).__name__,
                provider_calls=terminal_metadata(observations),
            )
        finally:
            provider_observations.reset(token)
        case["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
        results.append(case)
    return results
