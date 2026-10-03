import random
from copy import deepcopy

import pytest

from pixel_station.poker import bot_decision_view, evaluate, new_game, public_view
from pixel_station.poker_strategy import (
    CARDS,
    SIMULATION_SAMPLES,
    STRATEGY_VERSION,
    _rank,
    strategy_context,
)


def acting_view(
    hole="As Ad",
    *,
    stack=1000,
    opponents=2,
    stage="preflop",
    board="",
    bet=0,
    facing=10,
    style="balanced",
):
    seats = [
        {
            "index": 0,
            "stack": stack,
            "bet": bet,
            "contribution": bet,
            "folded": False,
            "all_in": False,
            "hole": hole.split(),
        }
    ]
    for index in range(1, opponents + 1):
        amount = facing if index == opponents else 5
        seats.append(
            {
                "index": index,
                "stack": 1000 - amount,
                "bet": amount,
                "contribution": amount,
                "folded": False,
                "all_in": False,
                "hole": [],
            }
        )
    return {
        "id": "synthetic-table",
        "hand_number": 1,
        "actor": 0,
        "dealer": 0,
        "stage": stage,
        "board": board.split(),
        "big_blind": 10,
        "pot": sum(seat["contribution"] for seat in seats),
        "seats": seats,
        "style": style,
        "legal_actions": [
            {"action": "fold"},
            {"action": "call"},
            {"action": "raise", "min": max(20, facing * 2), "max": stack + bet},
            {"action": "all_in"},
        ],
    }


def test_hidden_state_and_requested_work_cannot_affect_deterministic_context():
    original = acting_view()
    before = deepcopy(original)
    poisoned = deepcopy(original)
    poisoned.update(
        deck=list(reversed(CARDS)),
        future_cards=["Ks", "Kd"],
        samples=10**12,
        history=[{"private": "private-secret"}],
        winners=["private-secret"],
    )
    for seat in poisoned["seats"][1:]:
        seat.update(hole=["As", "Ad"], name="private-secret", personality="aggressive")
    context = strategy_context(original)
    assert context == strategy_context(poisoned) == strategy_context(original)
    assert original == before
    assert context["version"] == STRATEGY_VERSION
    assert context["simulation"]["samples"] == SIMULATION_SAMPLES == 128
    assert "private-secret" not in str(context)


def test_production_sanitized_actor_view_supported_at_maximum_table_size():
    state = new_game(6, rng=random.Random(4))
    context = strategy_context(bot_decision_view(public_view(state, state["actor"])))
    assert context["observed"]["active_opponents"] == 5
    assert 0 <= context["simulation"]["equity_share_estimate"] <= 1
    assert context["guidance"]["style"] in {
        "balanced",
        "value_focused",
        "position_aware",
        "selective_pressure",
        "patient",
    }


@pytest.mark.parametrize(
    "view",
    [
        acting_view(board="2s 3s 4s"),
        acting_view(stage="flop", board="As 3s 4s"),
    ],
)
def test_future_or_duplicate_known_cards_rejected(view):
    with pytest.raises(ValueError):
        strategy_context(view)


def test_fast_simulation_rank_agrees_with_game_engine_for_all_supported_card_counts():
    rng = random.Random(42)
    for count in (5, 6, 7):
        for _ in range(100):
            cards = rng.sample(CARDS, count)
            assert _rank(cards) == evaluate(cards)


@pytest.mark.parametrize(
    "cards,notation,tier",
    [
        ("As Ad", "AA", "premium"),
        ("Ks Kd", "KK", "premium"),
        ("As Kd", "AKo", "premium"),
        ("As Qd", "AQo", "strong"),
        ("Js Jd", "JJ", "strong"),
        ("Qs Js", "QJs", "marginal"),
        ("7s 2d", "72o", "weak"),
        ("Js 2d", "J2o", "weak"),
    ],
)
def test_starting_hand_bins_are_transparent_conservative_descriptors(cards, notation, tier):
    hand = strategy_context(acting_view(cards))["hand"]
    assert hand["notation"] == notation and hand["preflop_tier"] == tier
    assert "Not solved ranges" in hand["tier_method"]


def test_premium_equity_orders_above_trash_with_explicit_sampling_assumptions():
    premium = strategy_context(acting_view("As Ad"))["simulation"]
    trash = strategy_context(acting_view("7s 2d"))["simulation"]
    assert premium["equity_share_estimate"] > trash["equity_share_estimate"] + 0.25
    assert 0 <= premium["sample_standard_error"] <= 0.05
    assert "Uniform random" in premium["assumptions"]
    assert "Not actual winning probability" in premium["limitations"]


def test_heads_up_price_stack_units_and_button_position():
    observed = strategy_context(acting_view(stack=995, bet=5, opponents=1))["observed"]
    assert observed["to_call"] == observed["call_cost"] == 5
    assert observed["pot"] == 15 and observed["pot_odds"] == 0.25
    assert observed["hero_stack_bb"] == 99.5
    assert observed["hero_total_stack_bb"] == observed["effective_stack_bb"] == 100
    assert observed["position"] == "button_small_blind"
    assert observed["round_commitment"] == 5
    assert observed["round_commitment_fraction"] == 0.005


def test_short_call_cost_is_capped_and_price_is_labeled_as_approximation():
    observed = strategy_context(acting_view(stack=20, facing=100, opponents=1))["observed"]
    assert observed["to_call"] == 100 and observed["call_cost"] == 20
    assert observed["call_stack_fraction"] == 1
    assert "side pots" in observed["pot_odds_scope"]


def test_weak_deep_pressure_discourages_commitment_without_changing_legality():
    view = acting_view("7s 2d", stack=1000, facing=300)
    before = deepcopy(view["legal_actions"])
    context = strategy_context(view)
    risk = context["guidance"]
    assert context["observed"]["facing_raise"]
    assert risk["weak_deep_jam_discouraged"] and risk["preflop_jam_posture"] == "avoid"
    assert risk["max_voluntary_commitment"] == 350
    assert risk["raise_total_min"] == 600 and risk["raise_total_max"] == 0
    assert risk["raise_candidates"] == []
    assert view["legal_actions"] == before


def test_premium_short_stack_retains_aggression_and_weak_medium_stack_has_risk_ceiling():
    short = strategy_context(acting_view("Ks Kd", stack=90, facing=30))["guidance"]
    assert short["stack_regime"] == "short"
    assert short["preflop_jam_posture"] == "short_stack_candidate"
    assert short["max_voluntary_commitment"] == 90
    assert short["raise_candidates"]
    medium = strategy_context(acting_view("Js 2d", stack=250))["guidance"]
    assert medium["stack_regime"] == "medium" and medium["weak_deep_jam_discouraged"]
    assert medium["max_voluntary_commitment"] == 87


def test_style_changes_small_sizing_nudge_only_and_honors_sanitized_top_level_style():
    balanced = strategy_context(acting_view())
    pressure_view = acting_view(style="selective_pressure")
    pressure_view["seats"][0]["personality"] = "conservative"
    pressure = strategy_context(pressure_view)
    assert pressure["guidance"]["style"] == "selective_pressure"
    assert pressure["simulation"] == balanced["simulation"]
    assert pressure["observed"] == balanced["observed"]
    for context in (balanced, pressure):
        guide = context["guidance"]
        assert abs(guide["style_aggression_nudge"]) <= 0.03
        assert 0.94 <= guide["style_size_multiplier"] <= 1.06
        assert all(
            guide["raise_total_min"] <= amount <= guide["raise_total_max"] < 100
            for amount in guide["raise_candidates"]
        )


def test_river_value_and_board_only_tie_are_distinguished():
    nuts = strategy_context(acting_view("As Kd", stage="river", board="Qs Jh Tc 2d 3c", facing=0))
    assert nuts["hand"]["made_category"] == "Straight"
    assert nuts["hand"]["improves_board_hand"]
    assert nuts["simulation"]["equity_share_estimate"] > 0.9
    board_only = strategy_context(
        acting_view("2d 3c", stage="river", board="As Ks Qs Js Ts", facing=0)
    )
    assert board_only["hand"]["made_category"] == "Straight flush"
    assert not board_only["hand"]["improves_board_hand"]
    assert board_only["simulation"]["equity_share_estimate"] == 0.3333
    assert board_only["simulation"]["sample_standard_error"] == 0


def test_flush_draw_requires_own_card_participation():
    own_draw = strategy_context(acting_view("As Ks", stage="flop", board="Qs 2s 3d"))
    board_draw = strategy_context(acting_view("Ad Kd", stage="turn", board="Qs 2s 3s 4s"))
    assert own_draw["hand"]["flush_draw"]
    assert not board_draw["hand"]["flush_draw"]
