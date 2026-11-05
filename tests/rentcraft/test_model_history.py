"""Accepted engine history, complete-start evidence and strict persistence boundaries."""
import copy

import pytest

from boardcraft.contracts import Action as PublicAction, GameConfig
from boardcraft.games.rentcraft.actions import Phase
from boardcraft.games.rentcraft.module import RentcraftModule
from boardcraft.games.rentcraft import memory, observation
from boardcraft.games.rentcraft.state import AuctionState, TradeWindow
from .test_rules import act


def fresh(n=3, windows="off"):
    return RentcraftModule().new(GameConfig("rentcraft", "base", n, {"trade_windows": windows}), 129)


def context(game, viewer):
    return game.observe(viewer).data["model_context"]


def test_fresh_empty_history_is_complete_and_restores_without_fabrication():
    game = fresh()
    seat = game.decision().player
    ctx = context(game, seat)
    assert ctx == {"schema": 1, "complete_from_start": True, "turn": 1, "snapshot": None, "own_acts": []}
    assert observation.require_model_context(game.observe(seat)) == ctx
    history = game.game.state.memory.model_history
    assert history.schema == 1 and history.last_current == seat
    assert history.snaps == {} and history.own_acts == []
    restored = RentcraftModule().restore(game.snapshot())
    assert restored.snapshot() == game.snapshot()
    assert context(restored, seat) == ctx


@pytest.mark.parametrize("missing", ["absent", "null", "incomplete"])
def test_old_or_incomplete_history_remains_playable_but_refuses_model_context(missing):
    game = fresh()
    saved = game.snapshot()
    if missing == "absent":
        saved["state"]["memory"].pop("model_history", None)
    elif missing == "null":
        saved["state"]["memory"]["model_history"] = None
    else:
        saved["state"]["memory"]["model_history"]["complete_from_start"] = False
    restored = RentcraftModule().restore(saved)
    seat = restored.decision().player
    assert context(restored, seat)["complete_from_start"] is False
    with pytest.raises(ValueError, match="complete"):
        observation.require_model_context(restored.observe(seat))
    act(restored, "propose_trade", target=(seat + 1) % 3, give_cash=1)
    assert context(restored, seat)["complete_from_start"] is False
    with pytest.raises(ValueError, match="complete"):
        observation.require_model_context(restored.observe(seat))
    assert memory.Memory.new(3).model_history is None


def test_end_of_turn_snapshot_uses_post_remember_state_and_is_detached():
    game = fresh()
    s = game.game.state
    seat = s.current
    s.phase = Phase.POST_ROLL
    s.owner[1], s.mortgaged[1] = seat, True
    s.players[seat].cash = 1400
    s.memory.paid[seat][(seat + 1) % 3] = 17
    rng = game.game.rng.getstate()
    act(game, "end_turn")
    assert game.game.rng.getstate() == rng
    snap = context(game, seat)["snapshot"]
    assert snap["cash"][seat] == 1400 and snap["worth"][seat] == 1430
    assert snap["owned"][seat] == snap["mortgaged"][seat] == 1
    assert snap["buildings"] == [0, 0, 0] and snap["paid"][seat][(seat + 1) % 3] == 17
    assert context(game, (seat + 1) % 3)["snapshot"] is None
    s.players[seat].cash = 1
    s.memory.paid[seat][(seat + 1) % 3] = 99
    assert context(game, seat)["snapshot"] == snap
    assert game.game.state.memory.model_history.last_current == s.current
    assert RentcraftModule().restore(game.snapshot()).snapshot() == game.snapshot()


def test_pre_transition_window_phase_trigger_and_snapshot_order():
    game = fresh(windows="full")
    s = game.game.state
    seat = s.current
    s.phase = Phase.POST_ROLL
    act(game, "end_turn")
    assert s.phase is Phase.TRADE_WINDOW
    while s.window:
        actor, window, trigger = game.decision().player, s.window.kind, s.window.trigger
        act(game, "pass")
        row = s.memory.model_history.own_acts[-1]
        assert (row.kind, row.who, row.phase, row.window, row.trigger, row.turn) == (
            "pass_window", actor, "trade_window", window, trigger, 1)
    assert s.turn == 2
    assert context(game, actor)["own_acts"] == []
    assert context(game, seat)["snapshot"] is not None


def test_64_accepted_actions_per_seat_preserve_order_and_other_seats():
    game = fresh()
    s = game.game.state
    first, second = s.current, (s.current + 1) % 3
    s.owner[1], s.owner[3] = first, second
    # Mortgage/unmortgage are legal accepted commands; the rule engine permits reversals.
    act(game, "mortgage", space=1)
    s.phase = Phase.TRADE_WINDOW
    s.window = TradeWindow("request", None, [second], "restore", Phase.PRE_ROLL, None, 1)
    for _ in range(33):
        act(game, "mortgage", space=3)
        act(game, "unmortgage", space=3)
    assert len(context(game, first)["own_acts"]) == 1
    rows = context(game, second)["own_acts"]
    assert len(rows) == 64 and [r["kind"] for r in rows[:2]] == ["mortgage", "unmortgage"]
    assert all(r["who"] == second and r["window"] == "request" and r["trigger"] is None for r in rows)
    act(game, "pass")
    act(game, "roll")
    # Finish any purchase and the turn, then the first action of the next turn clears old actions.
    if s.phase is Phase.BUY_DECISION:
        act(game, "buy")
    while s.phase is Phase.POST_ROLL and s.extra_roll:
        act(game, "roll")
        if s.phase is Phase.BUY_DECISION:
            act(game, "buy")
    s.phase, s.extra_roll = Phase.POST_ROLL, False
    act(game, "end_turn")
    assert context(game, second)["own_acts"] == []
    assert RentcraftModule().restore(game.snapshot()).snapshot() == game.snapshot()


def test_auction_bid_and_winner_recorded_even_when_another_seat_closes_it():
    game = fresh(2)
    s = game.game.state
    first, other = s.current, (s.current + 1) % 2
    s.phase, s.auction, s.resume_phase = Phase.AUCTION, AuctionState(1, [first, other]), Phase.POST_ROLL
    act(game, "bid", amount=10)
    assert context(game, first)["own_acts"][-1]["amount"] == 10
    act(game, "pass_bid")
    rows = context(game, first)["own_acts"]
    assert [(r["kind"], r["space"], r["amount"], r["phase"]) for r in rows] == [
        ("bid", 1, 10, "auction"), ("auction_won", 1, 10, "auction")]
    assert context(game, other)["own_acts"] == []
    won = next(entry for entry in s.memory.log if entry.kind == "auction_won")
    assert won.bids == ((10, 0) if first == 0 else (0, 10))


@pytest.mark.parametrize("response,outcome", [("accept_trade", "accepted"), ("reject_trade", "rejected"),
                                              ("counter_trade", "countered")])
def test_offer_outcome_and_response_perspective(response, outcome):
    game = fresh()
    s = game.game.state
    first, other = s.current, (s.current + 1) % 3
    act(game, "propose_trade", target=other, give_cash=17)
    sent = context(game, first)["own_acts"][-1]
    assert (sent["kind"], sent["amount"], sent["to"], sent["outcome"], sent["phase"]) == (
        "trade_offer", 17, other, "pending", "pre_roll")
    act(game, response, **({"give_cash": 9} if response == "counter_trade" else {}))
    sent = context(game, first)["own_acts"][-1]
    assert sent["outcome"] == outcome and sent["phase"] == "pre_roll"
    reply = context(game, other)["own_acts"][-1]
    assert reply["to"] == first and reply["phase"] == "trade_response"
    if response != "counter_trade":
        assert reply["amount"] == -17 and reply["cards"] == [0, 0]
    assert RentcraftModule().restore(game.snapshot()).snapshot() == game.snapshot()


def test_rejected_command_does_not_record_history_or_consume_rng():
    game = fresh()
    before = game.snapshot()
    with pytest.raises(ValueError):
        game.apply(game.decision().player, PublicAction("invalid", "invalid", {}), game.decision().id)
    assert game.snapshot() == before


@pytest.mark.parametrize("field,value", [
    ("schema", True), ("schema", 2), ("complete_from_start", 1), ("last_current", 9),
    ("last_current", -2), ("own_acts", {}), ("snaps", {"9": {}}), ("private_draft", {}),
])
def test_malformed_model_history_is_rejected(field, value):
    game = fresh()
    saved = game.snapshot()
    saved["state"]["memory"]["model_history"][field] = value
    with pytest.raises(ValueError):
        RentcraftModule().restore(saved)


@pytest.mark.parametrize("field,value", [
    ("who", 9), ("to", -1), ("turn", 0), ("turn", 999), ("phase", "secret"),
    ("window", "secret"), ("trigger", 9), ("kind", "draft_abandoned"), ("amount", True),
    ("cards", [3, 0]), ("give", [0]), ("get", [1, 1]), ("outcome", "secret"),
    ("space", 0), ("origin", "private"),
])
def test_malformed_accepted_action_is_rejected(field, value):
    game = fresh()
    seat = game.decision().player
    act(game, "propose_trade", target=(seat + 1) % 3, give_cash=17)
    saved = game.snapshot()
    saved["state"]["memory"]["model_history"]["own_acts"][0][field] = value
    with pytest.raises(ValueError):
        RentcraftModule().restore(saved)


def test_malformed_snapshot_and_per_seat_overflow_are_rejected():
    game = fresh()
    seat = game.decision().player
    act(game, "propose_trade", target=(seat + 1) % 3, give_cash=17)
    saved = game.snapshot()
    h = saved["state"]["memory"]["model_history"]
    for field, value in [("cash", [-1] * 3), ("worth", [1]), ("paid", [[0] * 2] * 3),
                         ("owned", [29] * 3), ("buildings", [141] * 3), ("mortgaged", [29] * 3),
                         ("secret", [])]:
        damaged = copy.deepcopy(saved)
        damaged["state"]["memory"]["model_history"]["snaps"] = {str(seat): {
            "cash": [1500] * 3, "worth": [1500] * 3, "owned": [0] * 3, "buildings": [0] * 3,
            "mortgaged": [0] * 3, "paid": [[0] * 3 for _ in range(3)], field: value}}
        with pytest.raises(ValueError):
            RentcraftModule().restore(damaged)
    h["own_acts"] *= 65
    with pytest.raises(ValueError):
        RentcraftModule().restore(saved)


@pytest.mark.parametrize("kind,field,value", [
    ("buy", "space", None), ("decline", "space", None), ("build", "space", None),
    ("mortgage", "space", None), ("unmortgage", "space", None), ("bid", "space", None),
    ("auction_won", "space", None), ("interest", "space", None),
    ("pass_window", "space", 1), ("pass_window", "amount", 10),
    ("use_jail_card", "space", 1), ("use_jail_card", "amount", 10),
    ("jail_fine", "space", 1), ("decline", "amount", 10),
])
def test_accepted_action_payload_shape_must_match_its_kind(kind, field, value):
    game = fresh()
    seat = game.decision().player
    game.game.state.owner[1] = seat
    act(game, "mortgage", space=1)
    saved = game.snapshot()
    row = saved["state"]["memory"]["model_history"]["own_acts"][0]
    row.update(kind=kind, space=1, amount=0)
    if kind in ("pass_window", "use_jail_card", "jail_fine"):
        row["space"] = None
    row[field] = value
    with pytest.raises(ValueError):
        RentcraftModule().restore(saved)
