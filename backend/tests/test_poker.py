import random

import pytest

from pixel_station.poker import (
    act,
    evaluate,
    finish,
    legal_actions,
    new_game,
    next_hand,
    public_view,
)


@pytest.mark.parametrize(
    "cards,category",
    [
        ("As Ks Qs Js Ts 2d 3h", 8),
        ("Ah Ad Ac As 2d 3h 4h", 7),
        ("Ah Ad Ac Ks Kd 2h 3d", 6),
        ("Ah Jh 9h 7h 2h Kd Qd", 5),
        ("As 2d 3h 4s 5s 9c Kc", 4),
        ("Ah Ad Ac 2s 4s 7c 9c", 3),
        ("Ah Ad Kc Ks 2s 4c 7c", 2),
        ("Ah Ad 2c 4s 6s 8c Tc", 1),
        ("Ah Kd 2c 4s 6s 8c Tc", 0),
    ],
)
def test_hand_categories(cards, category):
    assert evaluate(cards.split())[0] == category


def test_wheel_loses_to_six_high_and_kickers_decide():
    assert evaluate("As 2d 3h 4s 5s".split()) < evaluate("2s 3d 4h 5s 6s".split())
    assert evaluate("Ah Ad Kc Qc Jc".split()) > evaluate("Ah Ad Kc Qc Tc".split())


def test_heads_up_blinds_and_action_order():
    state = new_game(2, rng=random.Random(1))
    assert state["dealer"] == state["actor"] == 0
    assert state["seats"][0]["bet"] == 5
    assert state["seats"][1]["bet"] == 10
    act(state, "call")
    assert state["actor"] == 1
    act(state, "check")
    assert state["stage"] == "flop" and state["actor"] == 1


def test_hidden_cards_never_leak_to_other_players():
    state = new_game(6, rng=random.Random(1))
    for viewer in range(6):
        view = public_view(state, viewer)
        assert len(view["seats"][viewer]["hole"]) == 2
        assert all(seat["hole"] == [] for seat in view["seats"] if seat["index"] != viewer)
        assert "deck" not in view


def test_invalid_raise_does_not_mutate_state():
    state = new_game(3, rng=random.Random(2))
    before = repr(state)
    with pytest.raises(ValueError):
        act(state, "raise", 11)
    assert repr(state) == before
    with pytest.raises(ValueError):
        act(state, "check")


def test_all_in_runout_and_side_pots_conserve_chips():
    state = new_game(3, rng=random.Random(3))
    state["initial_chips"] = 600
    for seat, total in zip(state["seats"], [100, 200, 300], strict=True):
        seat["stack"] = total - seat["contribution"]
    while not state["completed"]:
        actions = {action["action"] for action in legal_actions(state)}
        act(state, "all_in" if "all_in" in actions else "call" if "call" in actions else "check")
    assert len(state["board"]) == 5 and state["stage"] == "showdown"
    assert sum(seat["stack"] for seat in state["seats"]) == 600


def test_side_pot_awarded_only_to_eligible_hands_and_uncalled_refund():
    state = new_game(3, rng=random.Random(3))
    state["board"] = "2s 3s 4d 8h 9c".split()
    for seat, cards, contribution in zip(
        state["seats"], ["As Ad", "Ks Kd", "Qs Qd"], [100, 200, 300], strict=True
    ):
        seat.update(hole=cards.split(), contribution=contribution, stack=0, folded=False)
    state["initial_chips"] = 600
    finish(state)
    assert [seat["stack"] for seat in state["seats"]] == [300, 200, 100]


def test_short_all_in_does_not_reopen_betting():
    state = new_game(3, rng=random.Random(4))
    act(state, "raise", 50)  # Minimum full raise increment is now 40.
    act(state, "call")
    third = state["seats"][state["actor"]]
    third["stack"] = 55 - third["bet"]
    state["initial_chips"] = sum(seat["stack"] + seat["contribution"] for seat in state["seats"])
    act(state, "all_in")
    assert state["current_bet"] == 55
    assert "raise" not in {option["action"] for option in legal_actions(state)}
    assert "all_in" not in {option["action"] for option in legal_actions(state)}


def test_fold_awards_pot_and_next_hand_rotates_dealer():
    state = new_game(3, rng=random.Random(4))
    act(state, "fold")
    act(state, "fold")
    assert state["completed"]
    dealer = state["dealer"]
    next_hand(state, random.Random(5))
    assert state["dealer"] == (dealer + 1) % 3
    assert state["hand_number"] == 2


@pytest.mark.parametrize("seed", range(30))
def test_random_legal_hands_terminate_and_conserve_chips(seed):
    rng = random.Random(seed)
    state = new_game(rng.randint(2, 6), rng=rng)
    for _ in range(100):
        if state["completed"]:
            break
        options = legal_actions(state)
        option = rng.choice(options)
        act(state, option["action"], option.get("min"))
        total = sum(seat["stack"] for seat in state["seats"])
        if not state["completed"]:
            total += sum(seat["contribution"] for seat in state["seats"])
        assert total == state["initial_chips"]
    assert state["completed"]
