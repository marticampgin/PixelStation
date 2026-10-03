"""Deterministic no-limit Hold'em; models receive only a seat's legal public view."""

import asyncio
import itertools
import json
import random
import time
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Literal

import httpx
from anyio import CancelScope
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import JSON, ForeignKey, select
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base, FrictionEvent, new_id, now
from .observability import provider_observations

RANKS = "23456789TJQKA"
SUITS = "shdc"
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
BOT_TIME_BUDGET = 45
BOT_MAX_MODEL_ACTIONS = 64


def record_event(state: dict, action: str, seat: int | None = None, amount: int = 0) -> None:
    """Persist public facts only, with a stable sequence across all hands."""
    state["event_sequence"] = state.get("event_sequence", 0) + 1
    state.setdefault("event_log", []).append(
        {
            "sequence": state["event_sequence"],
            "created_at": now(),
            "hand_number": state["hand_number"],
            "stage": state["stage"],
            "seat": seat,
            "action": action,
            "amount": amount,
            "pot": sum(player["contribution"] for player in state["seats"]),
        }
    )


def settlement_needed(state: dict) -> bool:
    if state["completed"]:
        return False
    live = [seat for seat in state["seats"] if not seat["folded"]]
    actionable = [seat for seat in live if not seat["all_in"]]
    return (
        len(live) <= 1
        or not state["pending"]
        or (
            len(actionable) <= 1
            and (not actionable or actionable[0]["bet"] >= state["current_bet"])
        )
    )


def evaluate_five(cards: list[str]) -> tuple[int, ...]:
    values = sorted((RANKS.index(card[0]) + 2 for card in cards), reverse=True)
    counts = Counter(values)
    groups = sorted(((count, rank) for rank, count in counts.items()), reverse=True)
    unique = sorted(counts, reverse=True)
    straight = (
        5
        if unique == [14, 5, 4, 3, 2]
        else (unique[0] if len(unique) == 5 and unique[0] - unique[-1] == 4 else 0)
    )
    flush = len({card[1] for card in cards}) == 1
    if flush and straight:
        return (8, straight)
    if groups[0][0] == 4:
        return (7, groups[0][1], groups[1][1])
    if groups[0][0] == 3 and groups[1][0] == 2:
        return (6, groups[0][1], groups[1][1])
    if flush:
        return (5, *values)
    if straight:
        return (4, straight)
    if groups[0][0] == 3:
        return (
            3,
            groups[0][1],
            *sorted((rank for rank in counts if rank != groups[0][1]), reverse=True),
        )
    pairs = sorted((rank for rank, count in counts.items() if count == 2), reverse=True)
    if len(pairs) == 2:
        return (2, *pairs, next(rank for rank in counts if rank not in pairs))
    if pairs:
        return (1, pairs[0], *sorted((rank for rank in counts if rank != pairs[0]), reverse=True))
    return (0, *values)


def evaluate(cards: list[str]) -> tuple[int, ...]:
    if len(cards) < 5 or len(set(cards)) != len(cards):
        raise ValueError("A hand needs at least five distinct cards")
    return max(evaluate_five(list(combo)) for combo in itertools.combinations(cards, 5))


def next_seat(state: dict, start: int, candidates: list[int]) -> int:
    return min(candidates, key=lambda index: (index - start - 1) % len(state["seats"]))


def pay(state: dict, seat: int, amount: int) -> None:
    player = state["seats"][seat]
    paid = min(amount, player["stack"])
    player["stack"] -= paid
    player["bet"] += paid
    player["contribution"] += paid
    player["all_in"] = player["stack"] == 0


def new_game(
    seats: int = 3, stack: int = 1000, small_blind: int = 5, big_blind: int = 10, rng=None
) -> dict:
    if not 2 <= seats <= 6 or not 0 < small_blind < big_blind <= stack:
        raise ValueError("Use 2–6 seats and positive blinds below the starting stack")
    personalities = [
        "balanced",
        "conservative",
        "aggressive",
        "unpredictable",
        "balanced",
        "conservative",
    ]
    state = {
        "id": new_id(),
        "seats": [
            {
                "index": i,
                "name": "You" if i == 0 else f"Pixel {i}",
                "stack": stack,
                "personality": personalities[i],
            }
            for i in range(seats)
        ],
        "small_blind": small_blind,
        "big_blind": big_blind,
        "dealer": -1,
        "hand_number": 0,
        "initial_chips": seats * stack,
        "completed": True,
        "event_sequence": 0,
        "event_log": [],
    }
    next_hand(state, rng)
    return state


def next_hand(state: dict, rng=None) -> None:
    if not state["completed"]:
        raise ValueError("Finish the current hand first")
    active = [seat["index"] for seat in state["seats"] if seat["stack"] > 0]
    if len(active) < 2 or 0 not in active:
        raise ValueError("The game is over. Start a new table")
    state.update(
        hand_number=state["hand_number"] + 1,
        dealer=next_seat(state, state["dealer"], active),
        stage="preflop",
        completed=False,
        board=[],
        history=[],
        winners=[],
        current_bet=state["big_blind"],
        last_raise=state["big_blind"],
        model_status="",
        last_acted={},
        pending=[],
    )
    deck = [rank + suit for rank in RANKS for suit in SUITS]
    (rng or random.SystemRandom()).shuffle(deck)
    state["deck"] = deck
    for seat in state["seats"]:
        seat.update(hole=[], bet=0, contribution=0, folded=seat["stack"] == 0, all_in=False)
    for _ in range(2):
        for index in active:
            state["seats"][index]["hole"].append(deck.pop())
    sb = state["dealer"] if len(active) == 2 else next_seat(state, state["dealer"], active)
    bb = next_seat(state, sb, active)
    pay(state, sb, state["small_blind"])
    record_event(state, "small_blind", sb, state["seats"][sb]["bet"])
    pay(state, bb, state["big_blind"])
    record_event(state, "big_blind", bb, state["seats"][bb]["bet"])
    state["history"] = [
        {
            "seat": sb,
            "action": "small_blind",
            "amount": state["seats"][sb]["bet"],
            "stage": "preflop",
        },
        {
            "seat": bb,
            "action": "big_blind",
            "amount": state["seats"][bb]["bet"],
            "stage": "preflop",
        },
    ]
    state["pending"] = [index for index in active if not state["seats"][index]["all_in"]]
    state["actor"] = next_seat(state, bb, state["pending"]) if state["pending"] else None
    settle_round(state)


def legal_actions(state: dict, seat: int | None = None) -> list[dict]:
    seat = state.get("actor") if seat is None else seat
    if state["completed"] or seat is None or seat != state["actor"]:
        return []
    player = state["seats"][seat]
    due = max(0, state["current_bet"] - player["bet"])
    actions = [{"action": "fold"}]
    actions.append(
        {"action": "check"} if not due else {"action": "call", "amount": min(due, player["stack"])}
    )
    maximum = player["bet"] + player["stack"]
    last_acted = state["last_acted"].get(str(seat))
    reopened = last_acted is None or state["current_bet"] - last_acted >= state["last_raise"]
    can_raise = reopened and any(
        other["index"] != seat and not other["folded"] and not other["all_in"]
        for other in state["seats"]
    )
    minimum = state["current_bet"] + state["last_raise"]
    if can_raise and maximum >= minimum:
        actions.append({"action": "raise", "min": minimum, "max": maximum})
    if maximum <= state["current_bet"] or can_raise:
        actions.append({"action": "all_in", "amount": player["stack"]})
    return actions


def finish(state: dict) -> None:
    live = [seat for seat in state["seats"] if not seat["folded"]]
    awards: dict[int, int] = defaultdict(int)
    if len(live) == 1:
        awards[live[0]["index"]] = sum(seat["contribution"] for seat in state["seats"])
        state["stage"] = "finished"
    else:
        state["stage"] = "showdown"
        ranks = {seat["index"]: evaluate(seat["hole"] + state["board"]) for seat in live}
        previous = 0
        for level in sorted(
            {seat["contribution"] for seat in state["seats"] if seat["contribution"]}
        ):
            contributors = [seat for seat in state["seats"] if seat["contribution"] >= level]
            pot = (level - previous) * len(contributors)
            previous = level
            if len(contributors) == 1:
                awards[contributors[0]["index"]] += pot  # Uncalled excess is returned.
                continue
            eligible = [seat["index"] for seat in contributors if not seat["folded"]]
            best = max(ranks[index] for index in eligible)
            winners = [index for index in eligible if ranks[index] == best]
            winners.sort(key=lambda index: (index - state["dealer"] - 1) % len(state["seats"]))
            share, remainder = divmod(pot, len(winners))
            for position, index in enumerate(winners):
                awards[index] += share + (position < remainder)
    for index, amount in awards.items():
        state["seats"][index]["stack"] += amount
    state["winners"] = [
        {
            "seat": index,
            "amount": amount,
            "hand": HAND_NAMES[evaluate(state["seats"][index]["hole"] + state["board"])[0]]
            if state["stage"] == "showdown" and not state["seats"][index]["folded"]
            else "Uncontested",
        }
        for index, amount in awards.items()
    ]
    state.update(completed=True, actor=None, pending=[])
    record_event(state, state["stage"])
    assert sum(seat["stack"] for seat in state["seats"]) == state["initial_chips"]


def settle_round(state: dict, *, one_stage: bool = False) -> None:
    while not state["completed"]:
        live = [seat for seat in state["seats"] if not seat["folded"]]
        if len(live) == 1:
            finish(state)
            return
        actionable = [seat for seat in live if not seat["all_in"]]
        state["pending"] = [
            index for index in state["pending"] if index in [seat["index"] for seat in actionable]
        ]
        if len(actionable) <= 1 and (
            not actionable or actionable[0]["bet"] >= state["current_bet"]
        ):
            state["pending"] = []
        if state["pending"]:
            return
        if state["stage"] == "river":
            finish(state)
            return
        state["deck"].pop()  # Burn card.
        count = 3 if state["stage"] == "preflop" else 1
        state["board"].extend(state["deck"].pop() for _ in range(count))
        state["stage"] = {"preflop": "flop", "flop": "turn", "turn": "river"}[state["stage"]]
        for player in state["seats"]:
            player["bet"] = 0
        state.update(current_bet=0, last_raise=state["big_blind"], last_acted={})
        state["pending"] = [seat["index"] for seat in actionable]
        state["actor"] = (
            next_seat(state, state["dealer"], state["pending"]) if state["pending"] else None
        )
        record_event(state, "deal")
        if one_stage:
            return


def act(state: dict, action: str, amount: int | None = None, *, settle: bool = True) -> None:
    available = {item["action"]: item for item in legal_actions(state)}
    if action not in available:
        raise ValueError("Action is not legal in the current state")
    seat = state["actor"]
    player = state["seats"][seat]
    old_bet = state["current_bet"]
    paid = 0
    if action == "fold":
        player["folded"] = True
    elif action == "call":
        paid = available[action]["amount"]
        pay(state, seat, paid)
    elif action in {"raise", "all_in"}:
        if action == "raise":
            if amount is None or not available[action]["min"] <= amount <= available[action]["max"]:
                raise ValueError("Raise amount must be a legal total bet for this round")
            paid = amount - player["bet"]
        else:
            paid = player["stack"]
        pay(state, seat, paid)
    state["last_acted"][str(seat)] = max(old_bet, player["bet"])
    state["pending"] = [index for index in state["pending"] if index != seat]
    if player["bet"] > old_bet:
        increment = player["bet"] - old_bet
        state["current_bet"] = player["bet"]
        if increment >= state["last_raise"]:
            state["last_raise"] = increment
            state["last_acted"] = {str(seat): player["bet"]}
            state["pending"] = [
                other["index"]
                for other in state["seats"]
                if other["index"] != seat and not other["folded"] and not other["all_in"]
            ]
        else:
            state["pending"] = sorted(
                set(state["pending"])
                | {
                    other["index"]
                    for other in state["seats"]
                    if other["index"] != seat
                    and not other["folded"]
                    and not other["all_in"]
                    and other["bet"] < state["current_bet"]
                }
            )
    state["history"].append(
        {"seat": seat, "action": action, "amount": paid, "stage": state["stage"]}
    )
    record_event(state, action, seat, paid)
    if state["pending"]:
        state["actor"] = next_seat(state, seat, state["pending"])
    else:
        state["actor"] = None
    if settle:
        settle_round(state)


def public_view(state: dict, viewer: int = 0) -> dict:
    fields = (
        "id",
        "hand_number",
        "stage",
        "board",
        "dealer",
        "actor",
        "history",
        "winners",
        "completed",
        "small_blind",
        "big_blind",
        "model_status",
    )
    result = {field: state[field] for field in fields}
    result["pot"] = sum(seat["contribution"] for seat in state["seats"])
    result["seats"] = [
        {
            **seat,
            "hole": seat["hole"]
            if seat["index"] == viewer or (state["stage"] == "showdown" and not seat["folded"])
            else [],
        }
        for seat in state["seats"]
    ]
    result["legal_actions"] = (
        legal_actions(state) if state["actor"] == viewer and not settlement_needed(state) else []
    )
    result["event_sequence"] = state.get("event_sequence", 0)
    result["event_log"] = state.get("event_log", [])
    result["needs_step"] = not state["completed"] and (
        state["actor"] != 0 or settlement_needed(state)
    )
    result["phase"] = (
        "complete" if state["completed"] else "waiting" if result["needs_step"] else "ready"
    )
    # Queued stream events must remain snapshots when the engine mutates later.
    return deepcopy(result)


class GameSession(Base):
    __tablename__ = "game_sessions"
    id: Mapped[str] = mapped_column(primary_key=True, default=new_id)
    game: Mapped[str] = mapped_column(default="poker")
    created_at: Mapped[str] = mapped_column(default=now)
    updated_at: Mapped[str] = mapped_column(default=now)
    state: Mapped[dict] = mapped_column(JSON)


class PokerAction(Base):
    __tablename__ = "poker_actions"
    id: Mapped[str] = mapped_column(primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("game_sessions.id", ondelete="CASCADE"), index=True
    )
    hand_number: Mapped[int] = mapped_column()
    seat: Mapped[int] = mapped_column()
    stage: Mapped[str] = mapped_column()
    action: Mapped[str] = mapped_column()
    amount: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[str] = mapped_column(default=now)


class TableRequest(BaseModel):
    seats: int = Field(default=3, ge=2, le=6)
    stack: int = Field(default=1000, ge=100, le=100000)
    small_blind: int = Field(default=5, ge=1)
    big_blind: int = Field(default=10, ge=2)


class ActionRequest(BaseModel):
    action: Literal["fold", "check", "call", "raise", "all_in"]
    amount: int | None = Field(default=None, ge=1)
    expected_sequence: int | None = Field(default=None, ge=0)


class StepRequest(BaseModel):
    expected_sequence: int = Field(ge=0)


class BotChoice(ActionRequest):
    reason_short: str = Field(default="", max_length=300)


async def run_bots(
    request: Request,
    state: dict,
    *,
    max_actions: int | None = None,
    defer_settle: bool = False,
    on_phase: Callable[[str, int], Awaitable[None]] | None = None,
) -> list[str]:
    from .harness import record_observation

    errors: list[str] = []
    phase_started = time.monotonic()
    deadline = phase_started + max(0, BOT_TIME_BUDGET - state.get("opponent_compute_seconds", 0))

    async def infer(model: str, actor: int, settings, attempt: int) -> BotChoice:
        if on_phase:
            await on_phase("queued", actor)
        async with request.app.state.model_queue.lock:
            if on_phase:
                await on_phase("choosing", actor)
            return await request.app.state.llm.structured(
                model,
                [
                    {
                        "role": "system",
                        "content": f"You play no-limit Texas Hold'em with a {state['seats'][actor]['personality']} style. Choose only a listed legal action. Raise amounts are total round bets. Never invent cards. Return JSON.",
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                key: value
                                for key, value in public_view(state, actor).items()
                                if key not in {"event_log", "event_sequence", "phase", "needs_step"}
                            }
                        )
                        + (
                            "\nPrevious action failed validation. Select a legal option."
                            if attempt
                            else ""
                        ),
                    },
                ],
                BotChoice,
                validation_retries=0,
                options={
                    "num_ctx": min(settings.context_tokens, 8192),
                    "num_predict": 150,
                    "temperature": 0.5,
                },
            )

    for step in range(100):
        actor = state.get("actor")
        if state["completed"] or actor in {None, 0}:
            return errors
        observations: list[dict] = []
        metrics_scope = provider_observations.set(observations)
        try:
            options = legal_actions(state)
            decision_started = time.monotonic()
            repairs = 0
            choice = None
            settings = request.app.state.settings()
            model = settings.roles.get("primary_chat", "")
            for attempt in range(2):
                if (
                    not model
                    or time.monotonic() >= deadline
                    or (state.get("opponent_action_count", 0) + step >= BOT_MAX_MODEL_ACTIONS)
                ):
                    break
                try:
                    repairs += int(attempt > 0)
                    validated_choice: BotChoice = await asyncio.wait_for(
                        infer(model, actor, settings, attempt),
                        timeout=min(15, max(0.1, deadline - time.monotonic())),
                    )
                    act(
                        state,
                        validated_choice.action,
                        validated_choice.amount,
                        settle=not defer_settle,
                    )
                    choice = validated_choice
                    break
                except asyncio.CancelledError:
                    record_observation(
                        request.app,
                        route="poker_bot",
                        model=model or None,
                        status="interrupted",
                        latency_ms=int((time.monotonic() - decision_started) * 1000),
                        metrics={
                            "repair_count": repairs,
                            "fallback_count": 0,
                            "bot_actions": 0,
                            "provider_calls": observations,
                        },
                    )
                    raise
                except (TimeoutError, ValueError, RuntimeError, httpx.HTTPError) as error:
                    errors.append(f"Invalid or unavailable poker model action: {str(error)[:200]}")
                    choice = None
            if choice is None:
                legal = {option["action"] for option in options}
                act(
                    state,
                    "check" if "check" in legal else "call" if "call" in legal else "fold",
                    settle=not defer_settle,
                )
                state["model_status"] = (
                    "Some opponents used legal fallback actions because local inference was unavailable or exceeded its budget."
                )
            record_observation(
                request.app,
                route="poker_bot",
                model=model or None,
                status="complete",
                latency_ms=int((time.monotonic() - decision_started) * 1000),
                metrics={
                    "repair_count": repairs,
                    "fallback_count": int(choice is None),
                    "bot_actions": 1,
                    "provider_calls": observations,
                },
            )
            if max_actions is not None and step + 1 >= max_actions:
                state["opponent_action_count"] = state.get("opponent_action_count", 0) + step + 1
                state["opponent_compute_seconds"] = state.get("opponent_compute_seconds", 0) + (
                    time.monotonic() - phase_started
                )
                return errors
        finally:
            provider_observations.reset(metrics_scope)
    raise RuntimeError("Poker action budget exceeded")


def create_poker_router() -> APIRouter:
    router = APIRouter(prefix="/api/poker", tags=["poker"])
    locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
    phases: dict[str, str] = {}

    def check_sequence(state: dict, expected: int | None, *, required: bool = False) -> None:
        if required and expected is None:
            raise HTTPException(
                422, "Reload the table before acting: expected_sequence is required"
            )
        if expected is not None and expected != state.get("event_sequence", 0):
            raise HTTPException(409, "This turn has already changed. Reload the saved table")

    def start_opponent_phase(state: dict) -> None:
        state["opponent_action_count"] = 0
        state["opponent_compute_seconds"] = 0

    def check_idle(identity: str) -> None:
        if locks[identity].locked():
            raise HTTPException(
                409, "Another action is in progress at this table. Resume the saved turn"
            )

    def save(request: Request, state: dict, errors: list[str]) -> None:
        with request.app.state.database.session() as session:
            record = session.get(GameSession, state["id"])
            if record is None:
                record = GameSession(id=state["id"], state=deepcopy(state))
                session.add(record)
            else:
                record.state = deepcopy(state)
                record.updated_at = now()
            known = list(
                session.scalars(
                    select(PokerAction).where(
                        PokerAction.session_id == state["id"],
                        PokerAction.hand_number == state["hand_number"],
                    )
                )
            )
            for item in state["history"][len(known) :]:
                session.add(
                    PokerAction(session_id=state["id"], hand_number=state["hand_number"], **item)
                )
            for error in errors:
                session.add(
                    FrictionEvent(
                        kind="invalid_poker_action",
                        details=error,
                        regression={"expected": "legal poker action"},
                    )
                )
            session.commit()

    def load(request: Request, identity: str) -> dict:
        with request.app.state.database.session() as session:
            record = session.get(GameSession, identity)
            if record is None:
                raise HTTPException(404, "Poker table not found")
            return record.state

    @router.get("/sessions")
    def list_sessions(request: Request):
        with request.app.state.database.session() as session:
            return [
                {
                    "id": record.id,
                    "updated_at": record.updated_at,
                    "hand_number": record.state["hand_number"],
                    "completed": record.state["completed"],
                }
                for record in session.scalars(
                    select(GameSession).order_by(GameSession.updated_at.desc()).limit(20)
                )
            ]

    @router.post("/sessions")
    async def create(body: TableRequest, request: Request, progressive: bool = False):
        try:
            state = new_game(**body.model_dump())
            start_opponent_phase(state)
            errors = [] if progressive else await run_bots(request, state)
            save(request, state, errors)
            return public_view(state)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.get("/sessions/{identity}")
    def get(identity: str, request: Request):
        view = public_view(load(request, identity))
        if identity in phases:
            view["phase"] = phases[identity]
        return view

    @router.post("/sessions/{identity}/actions")
    async def action(
        identity: str, body: ActionRequest, request: Request, progressive: bool = False
    ):
        check_idle(identity)
        async with locks[identity]:
            state = load(request, identity)
            check_sequence(state, body.expected_sequence, required=progressive)
            if state["actor"] != 0 or settlement_needed(state):
                raise HTTPException(409, "It is not your turn")
            try:
                act(state, body.action, body.amount, settle=not progressive)
                start_opponent_phase(state)
                errors = [] if progressive else await run_bots(request, state)
                save(request, state, errors)
                return public_view(state)
            except ValueError as error:
                raise HTTPException(422, str(error)) from error

    @router.post("/sessions/{identity}/next-hand")
    async def advance(
        identity: str,
        request: Request,
        progressive: bool = False,
        expected_sequence: int | None = None,
    ):
        check_idle(identity)
        async with locks[identity]:
            state = load(request, identity)
            check_sequence(state, expected_sequence, required=progressive)
            try:
                next_hand(state)
                start_opponent_phase(state)
                errors = [] if progressive else await run_bots(request, state)
                save(request, state, errors)
                return public_view(state)
            except ValueError as error:
                raise HTTPException(422, str(error)) from error

    @router.post("/sessions/{identity}/steps")
    async def step(identity: str, body: StepRequest, request: Request):
        """One actual opponent action or one street, never a buffered future hand."""
        check_idle(identity)
        lock = locks[identity]
        await lock.acquire()
        try:
            state = load(request, identity)
            check_sequence(state, body.expected_sequence)
            if not public_view(state)["needs_step"]:
                raise HTTPException(409, "No opponent action is pending")
        except BaseException:
            lock.release()
            raise

        async def events():
            queue: asyncio.Queue[dict | None] = asyncio.Queue()

            async def phase(value: str, actor: int) -> None:
                phases[identity] = value
                view = public_view(state)
                view["phase"] = value
                await queue.put({"type": "phase", "phase": value, "actor": actor, "state": view})

            async def produce() -> None:
                try:
                    if settlement_needed(state):
                        settle_round(state, one_stage=True)
                        errors = []
                    else:
                        errors = await run_bots(
                            request, state, max_actions=1, defer_settle=True, on_phase=phase
                        )
                    save(request, state, errors)
                    await queue.put({"type": "state", "state": public_view(state)})
                    await queue.put({"type": "done"})
                except asyncio.CancelledError:
                    # No mutation occurs during inference. Previously committed steps survive.
                    raise
                except Exception:
                    await queue.put(
                        {
                            "type": "error",
                            "error": "This turn could not finish. Resume the saved table",
                        }
                    )
                finally:
                    await queue.put(None)

            task = asyncio.create_task(produce())
            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    yield json.dumps(event) + "\n"
            finally:
                task.cancel()
                try:
                    # StreamingResponse's disconnect cancel scope must not skip lock cleanup.
                    with CancelScope(shield=True):
                        await asyncio.gather(task, return_exceptions=True)
                finally:
                    phases.pop(identity, None)
                    lock.release()

        return StreamingResponse(
            events(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    return router
