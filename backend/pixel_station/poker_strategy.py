"""Bounded Hold'em evidence from an acting player's view, never hidden table state.

Starting-hand bins and sizes are conservative heuristics, not solved ranges. The
simulation estimates showdown pot share against uniform random hidden opponents;
it is not the probability of winning the actual hand or a betting recommendation.
"""

import hashlib
import json
import math
import random
from collections import Counter

STRATEGY_VERSION = 1
SIMULATION_SAMPLES = 128
RANKS = "23456789TJQKA"
SUITS = "shdc"
RANK_VALUE = {rank: index + 2 for index, rank in enumerate(RANKS)}
CARDS = tuple(rank + suit for rank in RANKS for suit in SUITS)
HAND_NAMES = (
    "High card",
    "Pair",
    "Two pair",
    "Three of a kind",
    "Straight",
    "Flush",
    "Full house",
    "Four of a kind",
    "Straight flush",
)
STAGE_CARDS = {"preflop": 0, "flop": 3, "turn": 4, "river": 5}
STYLE_ALIASES = {
    "conservative": "patient",
    "aggressive": "selective_pressure",
    "unpredictable": "position_aware",
}


def _chips(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10_000_000:
        raise ValueError("Strategy context requires bounded nonnegative integer chips")
    return value


def _cards(value, count: int) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) != count
        or any(card not in CARDS for card in value)
    ):
        raise ValueError("Strategy context requires only currently visible valid cards")
    return list(value)


def _straight_high(values: set[int]) -> int:
    if 14 in values:
        values = values | {1}
    return next(
        (high for high in range(14, 4, -1) if all(high - step in values for step in range(5))), 0
    )


def _rank(cards: list[str]) -> tuple[int, ...]:
    """Direct best-five rank for 5–7 known/simulated cards; agrees with engine ranks."""
    values = [RANK_VALUE[card[0]] for card in cards]
    counts = Counter(values)
    ranked = sorted(counts, reverse=True)
    suits = Counter(card[1] for card in cards)
    flush_suit = next((suit for suit, count in suits.items() if count >= 5), None)
    flush_values = sorted(
        (RANK_VALUE[card[0]] for card in cards if card[1] == flush_suit), reverse=True
    )
    if flush_suit and (straight_flush := _straight_high(set(flush_values))):
        return (8, straight_flush)
    quads = [value for value in ranked if counts[value] == 4]
    if quads:
        return (7, quads[0], next(value for value in ranked if value != quads[0]))
    trips = [value for value in ranked if counts[value] >= 3]
    pairs = [value for value in ranked if counts[value] >= 2]
    if trips and (full_pairs := [value for value in pairs if value != trips[0]]):
        return (6, trips[0], full_pairs[0])
    if flush_suit:
        return (5, *flush_values[:5])
    if straight := _straight_high(set(ranked)):
        return (4, straight)
    if trips:
        return (3, trips[0], *[value for value in ranked if value != trips[0]][:2])
    if len(pairs) >= 2:
        return (2, *pairs[:2], next(value for value in ranked if value not in pairs[:2]))
    if pairs:
        return (1, pairs[0], *[value for value in ranked if value != pairs[0]][:3])
    return (0, *ranked[:5])


def _preflop(hole: list[str]) -> tuple[str, str]:
    high, low = sorted((RANK_VALUE[card[0]] for card in hole), reverse=True)
    suited = hole[0][1] == hole[1][1]
    notation = RANKS[high - 2] + RANKS[low - 2] + ("" if high == low else "s" if suited else "o")
    if (high == low and high >= 12) or (high, low) == (14, 13):
        tier = "premium"
    elif (
        (high == low and high >= 10)
        or (high, low) == (14, 12)
        or (suited and (high, low) in {(14, 11), (13, 12)})
    ):
        tier = "strong"
    elif high == low or low >= 10 or (suited and (high == 14 or (high >= 6 and high - low <= 1))):
        tier = "marginal"
    else:
        tier = "weak"
    return notation, tier


def _equity(hole: list[str], board: list[str], opponents: int, seed: int) -> dict:
    remaining = [card for card in CARDS if card not in hole and card not in board]
    rng = random.Random(seed)
    total = squares = 0.0
    needed_board = 5 - len(board)
    for _ in range(SIMULATION_SAMPLES):
        drawn = rng.sample(remaining, needed_board + 2 * opponents)
        runout = board + drawn[:needed_board]
        hero = _rank(hole + runout)
        ranks = [hero] + [
            _rank(drawn[needed_board + 2 * index : needed_board + 2 * index + 2] + runout)
            for index in range(opponents)
        ]
        best = max(ranks)
        share = 1 / ranks.count(best) if hero == best else 0.0
        total += share
        squares += share * share
    mean = total / SIMULATION_SAMPLES
    variance = max(0.0, (squares - SIMULATION_SAMPLES * mean * mean) / (SIMULATION_SAMPLES - 1))
    return {
        "equity_share_estimate": round(mean, 4),
        "sample_standard_error": round(math.sqrt(variance / SIMULATION_SAMPLES), 4),
        "samples": SIMULATION_SAMPLES,
        "method": "deterministic seeded Monte Carlo showdown pot-share estimate",
        "assumptions": "Uniform random unseen opponent cards and runouts; split ties; currently active opponents only.",
        "limitations": "Not actual winning probability or solved call EV; ignores action ranges, fold equity, realization, rake and side-pot eligibility. Sampling noise can affect close decisions.",
    }


def _position(seats: list[dict], actor: int, dealer: int, stage: str) -> tuple[str, int]:
    dealt = [seat["index"] for seat in seats if seat["stack"] + seat["contribution"] > 0]
    count = len(seats)

    def following(start):
        return min(dealt, key=lambda index: (index - start - 1) % count)

    small = dealer if len(dealt) == 2 else following(dealer)
    big = following(small)
    actionable = [seat["index"] for seat in seats if not seat["folded"] and not seat["all_in"]]
    anchor = big if stage == "preflop" else dealer
    ordered = sorted(actionable, key=lambda index: (index - anchor - 1) % count)
    offset = ordered.index(actor)
    after = len(ordered) - offset - 1
    if actor == dealer:
        label = "button_small_blind" if len(dealt) == 2 else "button"
    elif actor == small:
        label = "small_blind"
    elif actor == big:
        label = "big_blind"
    else:
        label = "early" if offset == 0 else "late" if after <= 1 else "middle"
    return label, after


def strategy_context(view: dict) -> dict:
    """Use an explicit public-field allowlist plus the acting seat's two cards only.

    Opponent pockets, deck, history, future-card hints, names, and unknown fields
    cannot influence evidence, the sampling seed, or style variation.
    """
    stage = view.get("stage")
    actor = view.get("actor")
    raw_seats = view.get("seats")
    if stage not in STAGE_CARDS or not isinstance(raw_seats, list) or not 2 <= len(raw_seats) <= 6:
        raise ValueError("Strategy context requires an active 2–6 seat Hold'em view")
    if isinstance(actor, bool) or not isinstance(actor, int) or actor not in range(len(raw_seats)):
        raise ValueError("Strategy context requires a valid acting seat")
    seats = []
    for seat in raw_seats:
        index = seat.get("index")
        if isinstance(index, bool) or not isinstance(index, int):
            raise ValueError("Strategy context requires numbered seats")
        seats.append(
            {
                "index": index,
                "stack": _chips(seat.get("stack")),
                "bet": _chips(seat.get("bet", 0)),
                "contribution": _chips(seat.get("contribution", 0)),
                "folded": bool(seat.get("folded", False)),
                "all_in": bool(seat.get("all_in", False)),
            }
        )
    if sorted(seat["index"] for seat in seats) != list(range(len(seats))):
        raise ValueError("Strategy context requires distinct contiguous seat indexes")
    seats.sort(key=lambda seat: seat["index"])
    hero = seats[actor]
    if hero["folded"] or hero["all_in"] or not hero["stack"]:
        raise ValueError("Strategy context requires a player able to act")
    raw_hero = next(seat for seat in raw_seats if seat["index"] == actor)
    hole = _cards(raw_hero.get("hole"), 2)
    board = _cards(view.get("board", []), STAGE_CARDS[stage])
    if len(set(hole + board)) != len(hole + board):
        raise ValueError("Known cards must be distinct")
    opponents = [seat for seat in seats if seat["index"] != actor and not seat["folded"]]
    if not opponents:
        raise ValueError("Strategy context requires an active opponent")
    bb = _chips(view.get("big_blind"))
    if not bb:
        raise ValueError("A positive big blind is required")
    dealer = view.get("dealer")
    if isinstance(dealer, bool) or not isinstance(dealer, int) or dealer not in range(len(seats)):
        raise ValueError("Strategy context requires a valid dealer")
    pot = _chips(view.get("pot"))
    current_bet = max(seat["bet"] for seat in seats)
    to_call = max(0, current_bet - hero["bet"])
    call_cost = min(to_call, hero["stack"])
    total_stack = hero["stack"] + hero["bet"]
    position, players_after = _position(seats, actor, dealer, stage)
    notation, tier = _preflop(hole)
    known_rank = (
        _rank(hole + board)
        if board
        else ((1, RANK_VALUE[hole[0][0]]) if hole[0][0] == hole[1][0] else (0,))
    )
    board_suits = Counter(card[1] for card in board)
    known_suits = Counter(card[1] for card in hole + board)
    flush_draw = len(board) in {3, 4} and any(
        count == 4 and any(card[1] == suit for card in hole) for suit, count in known_suits.items()
    )
    board_values = {RANK_VALUE[card[0]] for card in board}
    window_values = board_values | ({1} if 14 in board_values else set())
    connected = max(
        (sum(high - step in window_values for step in range(5)) for high in range(5, 15)), default=0
    )
    public_seed = {
        "table": str(view.get("id", ""))[:128],
        "hand": _chips(view.get("hand_number", 0)),
        "actor": actor,
        "dealer": dealer,
        "stage": stage,
        "hole": sorted(hole),
        "board": sorted(board),
        "seats": seats,
        "pot": pot,
        "big_blind": bb,
    }
    seed = int.from_bytes(
        hashlib.sha256(json.dumps(public_seed, sort_keys=True).encode()).digest()[:8]
    )
    simulation = _equity(hole, board, len(opponents), seed)
    requested_style = str(view.get("style", raw_hero.get("personality", "balanced")))
    style = STYLE_ALIASES.get(requested_style, requested_style)
    biases = {
        "balanced": 0.0,
        "value_focused": -0.01,
        "position_aware": 0.01 if position.startswith("button") or position == "late" else -0.01,
        "selective_pressure": 0.02,
        "patient": -0.02,
    }
    if style not in biases:
        style = "balanced"
    variation = random.Random(seed ^ 0x504958454C).uniform(-0.0075, 0.0075)
    nudge = round(biases[style] + variation, 4)
    size_multiplier = round(1 + nudge * 2, 4)
    hero_bb = total_stack / bb
    regime = "short" if hero_bb <= 15 else "medium" if hero_bb < 40 else "deep"
    weak_deep = stage == "preflop" and hero_bb > 20 and tier in {"weak", "marginal"}
    voluntary_cap = max(0, int(hero["stack"] * 0.35)) if weak_deep else hero["stack"]
    raise_option = next(
        (option for option in view.get("legal_actions", []) if option.get("action") == "raise"),
        None,
    )
    candidates = []
    raise_min = raise_max = None
    if raise_option:
        minimum, maximum = _chips(raise_option["min"]), _chips(raise_option["max"])
        raise_min, raise_max = minimum, 0
        if stage == "preflop":
            callers = max(
                0, sum(seat["bet"] == current_bet and not seat["folded"] for seat in opponents) - 1
            )
            base = (
                (3 * current_bet + 0.5 * current_bet * callers)
                if current_bet > bb
                else bb * (2.5 + 0.75 * callers)
            )
            size_cap = math.ceil(base * 1.5)
        else:
            base = current_bet + 0.6 * (pot + call_cost)
            size_cap = current_bet + math.ceil(0.85 * (pot + call_cost))
        maximum = min(maximum, hero["bet"] + voluntary_cap, size_cap)
        if maximum >= minimum:
            raise_min, raise_max = minimum, maximum
            candidates = sorted(
                {
                    max(minimum, min(maximum, round(base * size_multiplier * factor)))
                    for factor in (0.9, 1.0, 1.15)
                }
            )
    return {
        "version": STRATEGY_VERSION,
        "observed": {
            "to_call": to_call,
            "call_cost": call_cost,
            "pot": pot,
            "pot_odds": round(call_cost / (pot + call_cost), 4) if call_cost else 0.0,
            "pot_odds_scope": "Cost/(current pot+cost), heads-up single-pot approximation; side pots and future betting excluded.",
            "hero_stack_bb": round(hero["stack"] / bb, 2),
            "hero_total_stack_bb": round(hero_bb, 2),
            "effective_stack_bb": round(
                min(total_stack, max(seat["stack"] + seat["bet"] for seat in opponents)) / bb, 2
            ),
            "round_commitment": hero["bet"],
            "round_commitment_fraction": round(hero["bet"] / total_stack, 4),
            "call_stack_fraction": round(call_cost / hero["stack"], 4),
            "position": position,
            "players_after_hero": players_after,
            "active_opponents": len(opponents),
            "actionable_opponents": sum(not seat["all_in"] for seat in opponents),
            "facing_raise": stage == "preflop" and current_bet > bb,
        },
        "hand": {
            "notation": notation,
            "preflop_tier": tier,
            "tier_method": "Conservative fixed bins: QQ+/AK premium; TT-JJ/AQ/AJs/KQs strong; other pairs/broadways/suited aces/connectors marginal. Not solved ranges.",
            "made_category": HAND_NAMES[known_rank[0]],
            "improves_board_hand": _rank(hole + board) > _rank(board) if len(board) == 5 else None,
            "board_texture": {
                "paired": len(board_values) < len(board),
                "max_suit_count": max(board_suits.values(), default=0),
                "connected_cards_within_five": connected,
            },
            "flush_draw": flush_draw,
        },
        "simulation": simulation,
        "guidance": {
            "stack_regime": regime,
            "preflop_jam_posture": "short_stack_candidate"
            if stage == "preflop" and regime == "short" and tier in {"strong", "premium"}
            else "selective"
            if stage == "preflop" and tier == "premium"
            else "avoid",
            "weak_deep_jam_discouraged": weak_deep,
            "max_voluntary_commitment": voluntary_cap,
            "commitment_units": "Additional chips this decision; weak/marginal preflop ceiling above 20 BB is 35% of remaining stack.",
            "raise_total_min": raise_min,
            "raise_total_max": raise_max,
            "raise_candidates": candidates,
            "raise_units": "Total round bet; bounded ordinary sizing cues, not optimal sizing or a legal-action replacement.",
            "style": style,
            "style_aggression_nudge": nudge,
            "style_size_multiplier": size_multiplier,
            "caution": "Listed all-in is permission, not advice. Avoid weak deep preflop jams/large commitments; premium and short-stack aggression still need price/range judgment. Evidence and risk dominate tiny style variation; compare estimated equity cautiously, never as solved EV.",
        },
    }
