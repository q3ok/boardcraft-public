import json

import pytest

from boardcraft.contracts import Action, IllegalAction
from boardcraft.games.rentcraft.actions import Phase
from boardcraft.games.rentcraft.module import RentcraftModule
from .test_rules import act, table


def test_net_worth_placements():
    g = table(4, max_rounds=1)
    s = g.game.state
    for p, amount in zip(s.players, [110, 50, 150, 100]):
        p.cash = amount
    s.phase, s.current = Phase.POST_ROLL, 3
    act(g, "end_turn")
    result = g.result()
    assert result.placements == (2, 4, 1, 3)
    assert result.scores == (110, 50, 150, 100)
    assert result.reason == "round_limit"
    assert result.details == {"winner": 2, "rounds": 1, "by_round_limit": True}


def test_ties_and_later_bankruptcy():
    g = table(4, max_rounds=1)
    for rev, actor in enumerate([1, 2]):
        g.apply_seat_operation(actor, Action("resign", "resign", {}), rev)
    s = g.game.state
    s.phase, s.current = Phase.POST_ROLL, 3
    act(g, "end_turn")
    assert g.result().placements == (1, 4, 3, 2)


def test_restore_nested_decision():
    g = table()
    g.game.state.pending_buy, g.game.state.phase = 9, Phase.BUY_DECISION
    act(g, "decline")
    g.apply_seat_operation(2, Action("request_trade", "request_trade", {}), 1)
    act(g, "propose_trade", target=1, give_cash=5)
    other = RentcraftModule().restore(json.loads(json.dumps(g.snapshot())))
    for kind, params in [("counter_trade", {"give_cash": 3}), ("accept_trade", {}),
                         ("bid", {"amount": 50}), ("pass_bid", {}), ("pass_bid", {})]:
        assert g.decision() == other.decision()
        assert act(g, kind, **params) == act(other, kind, **params)
        assert g.snapshot() == other.snapshot()


def test_stale_and_wrong_actor_leave_revision_unchanged():
    g = table()
    d = g.decision()
    for call in (lambda: g.apply(1, Action("roll", "roll", {}), d.id),
                 lambda: g.apply(0, Action("roll", "roll", {}), "expired"),
                 lambda: g.apply_seat_operation(1, Action("resign", "resign", {}), 1),
                 lambda: g.apply_seat_operation(1, Action("resign", "resign", {"unknown": 1}), 0)):
        before = g.snapshot()
        with pytest.raises(IllegalAction):
            call()
        assert g.snapshot() == before


def test_bad_private_deck_is_rejected():
    g = table()
    g.game.state.decks["opportunity"][0] = g.game.state.decks["opportunity"][1]
    with pytest.raises(ValueError):
        RentcraftModule().restore(g.snapshot())



@pytest.mark.parametrize("phase", ["raise_funds", "trade_window", "mortgage_takeover"])
def test_restore_offer_while_another_decision_waits(phase):
    from boardcraft.games.rentcraft.state import Debt

    g = table()
    s = g.game.state
    if phase == "raise_funds":
        s.phase, s.debts, s.owner[5] = Phase.RAISE_FUNDS, [Debt(0, 1, 1550)], 0
    elif phase == "trade_window":
        g.apply_seat_operation(2, Action("request_trade", "request_trade", {}), 0)
    else:
        s.owner[3], s.mortgaged[3] = 0, True
        act(g, "propose_trade", target=1, give_spaces=[3])
        act(g, "accept_trade")
    actor = g.decision().player
    act(g, "propose_trade", target=(actor + 1) % 3, give_cash=1)
    clone = RentcraftModule().restore(json.loads(json.dumps(g.snapshot())))
    assert act(g, "reject_trade") == act(clone, "reject_trade")
    assert g.snapshot() == clone.snapshot()
    while not g.is_over():
        active = next(p.id for p in g.game.state.players if not p.bankrupt)
        rev = g.observe(None).revision
        op = Action("resign", "resign", {})
        assert g.apply_seat_operation(active, op, rev) == clone.apply_seat_operation(active, op, rev)
    assert g.result() == clone.result()
    assert g.result().reason == "last_player"


def test_new_clone_restore_and_roll_share_the_same_seed():
    from boardcraft.contracts import GameConfig

    config = GameConfig("rentcraft", "base", 4, {"max_rounds": 1})
    games = [RentcraftModule().new(config, 871), RentcraftModule().new(config, 871)]
    games.extend([games[0].clone(), RentcraftModule().restore(games[0].snapshot())])
    for _ in range(300):
        first = games[0]
        if first.is_over():
            break
        d = first.decision()
        choices = first.legal_actions(d.player)
        chosen = next((a for k in ("roll", "decline", "pass_bid", "pass", "end_turn")
                       for a in choices if a.kind == k), choices[0])
        receipts = [g.apply(d.player, chosen, d.id) for g in games]
        assert all(receipt == receipts[0] for receipt in receipts)
    assert first.is_over()
    assert all(g.result() == first.result() for g in games)


@pytest.mark.parametrize("corrupt", [
    lambda state: state["pending_takeovers"].append([99, 5]),
    lambda state: state["trade_requests"].append(-1),
    lambda state: state["start_rolls"][0][1].__setitem__(0, 9),
    lambda state: state["pending_payments"].append([0, 1, 30, {"hidden": "future"}]),
    lambda state: state["players"][1].update(bankrupt=True, bankrupt_order=None),
])
def test_restore_rejects_malformed_pending_positions(corrupt):
    snapshot = table().snapshot()
    corrupt(snapshot["state"])
    with pytest.raises(ValueError):
        RentcraftModule().restore(snapshot)


def test_review_resigning_interest_debtor_resumes_original_auction():
    g = table()
    s = g.game.state
    s.phase, s.pending_buy = Phase.BUY_DECISION, 9
    s.owner[5], s.mortgaged[5], s.players[2].cash = 1, True, 0
    act(g, "decline")
    act(g, "propose_trade", target=2, give_spaces=[5])
    act(g, "accept_trade")
    act(g, "pass")
    assert g.decision().kind == "raise_funds"
    assert [(d.debtor, d.amount) for d in s.debts] == [(2, 10)]
    g.apply_seat_operation(2, Action("resign", "resign", {}), g.revision)
    assert s.debts == [] and s.pending_auctions == [5]
    assert (g.decision().kind, g.decision().player) == ("auction", 1)
    other = RentcraftModule().restore(g.snapshot())
    for kind, params in [("bid", {"amount": 10}), ("pass_bid", {})]:
        assert act(g, kind, **params) == act(other, kind, **params)
    assert s.owner[9] == 1 and s.auction.space == 5
    assert g.snapshot() == other.snapshot()


@pytest.mark.parametrize("field", ["proposals", "accepted", "rejected", "auctions_won", "auction_paid",
                                   "auction_list", "declined", "paid", "offers", "rejects", "streak",
                                   "last_cash", "last_turn", "accepted_pair", "countered", "pair_offers"])
def test_review_invalid_memory_dimensions(field):
    snapshot = table().snapshot()
    memory = snapshot["state"]["memory"]
    if isinstance(memory[field][0], list):
        memory[field][0].pop()
    else:
        memory[field].pop()
    with pytest.raises(ValueError):
        RentcraftModule().restore(snapshot)


@pytest.mark.parametrize("parent", ["window", "trade", "takeover", "outer", "resume"])
def test_review_invalid_return_context(parent):
    g = table()
    if parent == "window":
        g.apply_seat_operation(2, Action("request_trade", "request_trade", {}), 0)
    elif parent in ("trade", "outer"):
        act(g, "propose_trade", target=1, give_cash=5)
    elif parent == "takeover":
        g.game.state.owner[5], g.game.state.mortgaged[5] = 0, True
        act(g, "propose_trade", target=1, give_spaces=[5])
        act(g, "accept_trade")
    snapshot = g.snapshot()
    s = snapshot["state"]
    if parent == "window":
        s["window"]["phase"] = "trade_response"
    elif parent == "trade":
        s["trade"]["resume_phase"] = "trade_response"
    elif parent == "outer":
        s["trade"]["outer_resume"] = "buy_decision"
    elif parent == "takeover":
        s["takeover_return"][0] = "auction"
    else:
        s["resume_phase"] = "trade_window"
    with pytest.raises(ValueError):
        RentcraftModule().restore(snapshot)


def test_review_reject_cycle_between_trade_and_takeover():
    g = table()
    s = g.game.state
    s.owner[5], s.mortgaged[5] = 0, True
    act(g, "propose_trade", target=1, give_spaces=[5])
    act(g, "accept_trade")
    act(g, "propose_trade", target=0, give_cash=5)
    snapshot = g.snapshot()
    assert snapshot["state"]["trade"]["resume_phase"] == "mortgage_takeover"
    snapshot["state"]["takeover_return"][0] = "trade_response"
    with pytest.raises(ValueError):
        RentcraftModule().restore(snapshot)


def test_review_completed_auction_can_resume_from_acquisition_window():
    g = table(trade_windows="full")
    s = g.game.state
    s.phase, s.pending_buy = Phase.BUY_DECISION, 9
    for kind, params in [("decline", {}), ("bid", {"amount": 10}), ("pass_bid", {}), ("pass_bid", {})]:
        act(g, kind, **params)
    assert s.auction is None and s.window.kind == "acquisition"
    assert s.window.phase is Phase.AUCTION
    other = RentcraftModule().restore(g.snapshot())
    for kind in ("pass", "pass"):
        assert act(g, kind) == act(other, kind)
    assert g.decision().kind == "post_roll" and g.snapshot() == other.snapshot()


@pytest.mark.parametrize("owner_auctions", [False, True], ids=["buyer_in_request_window", "owner_leaves_window"])
def test_restore_resignation_continuation_through_public_play(owner_auctions):
    from boardcraft.contracts import GameConfig

    module = RentcraftModule()
    settings = {"trade_windows": "off"} if owner_auctions else {}
    live = module.new(GameConfig("rentcraft", "base", 3, settings), 50)
    resumed = module.restore(live.snapshot())

    def transition(kind, seat=None):
        nonlocal resumed
        assert live.decision() == resumed.decision()
        if seat is None:
            events = act(live, kind)
            replayed = act(resumed, kind)
        else:
            command = Action(kind, kind, {})
            events = live.apply_seat_operation(seat, command, live.observe(None).revision)
            replayed = resumed.apply_seat_operation(seat, command, resumed.observe(None).revision)
        assert replayed == events and resumed.snapshot() == live.snapshot()
        resumed = module.restore(json.loads(json.dumps(live.snapshot())))
        assert resumed.snapshot() == live.snapshot()
        assert resumed.observe(None) == live.observe(None)
        assert resumed.decision() == live.decision()
        return events

    assert live.decision().player == 2
    assert transition("roll")[0].payload["dice"] == [1, 2]
    assert live.observe(None).data["pending_buy"] == 3
    if owner_auctions:
        transition("buy")
        transition("end_turn")
        transition("request_trade", 2)
        transition("resign", 2)
        view = live.observe(None).data
        assert view["window"]["queue"] == [] and view["resume_phase"] == "trade_window"
        assert view["auction"]["space"] == 3
        transition("pass_bid")
        transition("pass_bid")
    else:
        transition("request_trade", 0)
        transition("resign", 2)
        view = live.observe(None).data
        assert view["pending_buy"] is None and view["window"]["phase"] == "buy_decision"
        assert view["players"][2]["bankrupt"]
        transition("pass")
    assert (live.decision().kind, live.decision().player) == ("pre_roll", 0)


def test_restore_still_requires_active_buyer_and_window_resources():
    from boardcraft.contracts import GameConfig

    module = RentcraftModule()
    g = module.new(GameConfig("rentcraft", "base", 3), 50)
    act(g, "roll")
    active_buy = g.snapshot()
    active_buy["state"]["pending_buy"] = None
    with pytest.raises(ValueError):
        module.restore(active_buy)

    g.apply_seat_operation(0, Action("request_trade", "request_trade", {}), g.revision)
    missing_purchase = g.snapshot()
    missing_purchase["state"]["pending_buy"] = None
    with pytest.raises(ValueError):
        module.restore(missing_purchase)
    missing_decider = g.snapshot()
    missing_decider["state"]["window"]["queue"] = []
    with pytest.raises(ValueError):
        module.restore(missing_decider)
