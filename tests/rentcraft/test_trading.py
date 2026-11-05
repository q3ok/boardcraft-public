import pytest

from boardcraft.contracts import Action, IllegalAction
from boardcraft.games.rentcraft.actions import Phase
from boardcraft.games.rentcraft.state import Debt
from .test_rules import act, table


def request(g, player):
    return g.apply_seat_operation(player, Action("request_trade", "request_trade", {}), g.observe(None).revision)


def test_trade_limits():
    g = table()
    for negotiation in range(3):
        act(g, "propose_trade", target=1, give_cash=1)
        for seat in (1, 0, 1):
            assert g.decision().player == seat
            act(g, "counter_trade", give_cash=3)
        before = g.snapshot()
        with pytest.raises(IllegalAction):
            act(g, "counter_trade", give_cash=2)
        assert g.snapshot() == before
        act(g, "reject_trade")
    with pytest.raises(IllegalAction):
        act(g, "propose_trade", target=1, give_cash=1)
    for _ in range(3):
        request(g, 2)
        assert g.decision().player == 2
        act(g, "propose_trade", target=1, give_cash=1)
        act(g, "reject_trade")
        assert g.decision().player == 0
    before = g.snapshot()
    with pytest.raises(IllegalAction):
        request(g, 2)
    assert g.snapshot() == before


def test_request_waits_for_pending_response():
    g = table()
    act(g, "propose_trade", target=1, give_cash=10)
    request(g, 2)
    assert g.decision().kind == "trade_response"
    act(g, "reject_trade")
    assert (g.decision().kind, g.decision().player) == ("trade_window", 2)


def test_cash_must_exist_when_accepting():
    g = table()
    s = g.game.state
    s.players[1].cash, s.owner[5] = 5, 1
    act(g, "propose_trade", target=1, get_cash=105)
    before = g.snapshot()
    with pytest.raises(IllegalAction):
        g.apply(1, Action("accept_trade", "accept_trade", {}), g.decision().id)
    assert g.snapshot() == before
    act(g, "mortgage", space=5)
    act(g, "accept_trade")
    assert [p.cash for p in s.players] == [1605, 0, 1500]


@pytest.mark.parametrize("params", [{"target": True}, {"target": 0, "give_cash": 1},
                                    {"target": 1, "get_spaces": [2, 2]}, {"target": 1, "get_cash": -1},
                                    {"target": 1, "give_cards": 2}, {"target": 1, "give_cash": False},
                                    {"target": 1, "get_cash": 3, "proposer": 2}])
def test_invalid_trade_is_atomic(params):
    g = table()
    before = g.snapshot()
    with pytest.raises(IllegalAction):
        act(g, "propose_trade", **params)
    assert g.snapshot() == before


@pytest.mark.parametrize("phase", ["auction", "raise_funds", "mortgage_takeover", "trade_window"])
def test_return_to_interrupted_phase(phase):
    g = table()
    s = g.game.state
    if phase == "auction":
        s.phase, s.pending_buy = Phase.BUY_DECISION, 9
        act(g, "decline")
    elif phase == "raise_funds":
        s.phase, s.debts = Phase.RAISE_FUNDS, [Debt(0, 1, 1550)]
        s.owner[5] = 0
    elif phase == "mortgage_takeover":
        s.owner[5], s.mortgaged[5] = 0, True
        act(g, "propose_trade", target=1, give_spaces=[5])
        act(g, "accept_trade")
    else:
        g = table(trade_windows="full")
        s = g.game.state
        s.phase = Phase.POST_ROLL
        act(g, "end_turn")
    player = g.decision().player
    assert g.decision().kind == phase
    act(g, "propose_trade", target=(player + 1) % 3, give_cash=3)
    act(g, "reject_trade")
    assert g.decision().kind == phase
    assert g.decision().player == (2 if phase == "trade_window" else player)



def test_zero_offer_limit_disables_negotiation():
    g = table(max_trade_exchanges=0)
    assert "propose_trade" not in {c["kind"] for c in g.command_specs(0)}
    with pytest.raises(IllegalAction):
        act(g, "propose_trade", target=1, give_cash=1)


@pytest.mark.parametrize("phase", ["auction", "raise_funds", "mortgage_takeover"])
def test_interrupted_decisions_have_independent_trade_allowances(phase):
    g = table()
    s = g.game.state
    s.trades_this_turn = 3
    if phase == "auction":
        s.phase, s.pending_buy = Phase.BUY_DECISION, 1
        act(g, "decline")
    elif phase == "raise_funds":
        s.phase, s.debts, s.owner[1] = Phase.RAISE_FUNDS, [Debt(0, 2, 1501)], 0
    else:
        s.owner[5], s.mortgaged[5] = 0, True
        s.pending_takeovers, s.takeover_return = [(0, 5)], (Phase.PRE_ROLL, None)
        s.phase = Phase.MORTGAGE_TAKEOVER
    actor = g.decision().player
    for _ in range(3):
        act(g, "propose_trade", target=(actor + 1) % 3, give_cash=1)
        act(g, "reject_trade")
    assert g.decision().kind == phase and s.trades_this_turn == 3
    with pytest.raises(IllegalAction):
        act(g, "propose_trade", target=(actor + 1) % 3, give_cash=1)


def test_acceptance_checks_titles_again_after_building():
    g = table()
    s = g.game.state
    s.owner[1] = s.owner[3] = 1
    act(g, "propose_trade", target=1, give_cash=70, get_spaces=[1])
    act(g, "build", space=3)
    before = g.snapshot()
    with pytest.raises(IllegalAction):
        g.apply(1, Action("accept_trade", "accept_trade", {}), g.decision().id)
    assert g.snapshot() == before
    act(g, "sell_building", space=3)
    act(g, "mortgage", space=1)
    act(g, "accept_trade")
    assert g.decision().kind == "mortgage_takeover" and s.owner[1] == 0


def test_trade_exchanges_cards_and_titles_in_both_directions():
    g = table()
    s = g.game.state
    s.owner[1], s.owner[3], s.owner[15] = 0, 0, 1
    for player, deck, card in [(0, "opportunity", 8), (1, "community_fund", 4)]:
        s.decks[deck].remove(card)
        s.players[player].jail_cards = [deck]
    act(g, "propose_trade", target=1, give_spaces=[1, 3], get_spaces=[15], give_cards=1, get_cards=1,
        give_cash=45, get_cash=30)
    act(g, "accept_trade")
    assert (s.owner[1], s.owner[3], s.owner[15]) == (1, 1, 0)
    assert [p.cash for p in s.players] == [1485, 1515, 1500]
    assert s.players[0].jail_cards == ["community_fund"] and s.players[1].jail_cards == ["opportunity"]


def test_review_duplicate_requests_do_not_invalidate_another_seat():
    g = table()
    act(g, "propose_trade", target=1, give_cash=1)
    first = request(g, 2)
    response, snapshot = g.decision(), g.snapshot()
    assert "request_trade" not in {c["kind"] for c in g.command_specs(2)}
    for _ in range(3):
        with pytest.raises(IllegalAction):
            request(g, 2)
    assert g.snapshot() == snapshot and g.project_events(first, None) == first
    g.apply(1, Action("accept_trade", "accept_trade", {}), response.id)
    assert g.decision().player == 2 and g.decision().kind == "trade_window"


def test_review_first_request_in_an_existing_window_is_recorded_once():
    g = table(trade_windows="full")
    g.game.state.phase = Phase.POST_ROLL
    act(g, "end_turn")
    assert g.game.state.window.requested == set()
    request(g, 1)
    assert g.game.state.window.requested == {1}
    snapshot = g.snapshot()
    with pytest.raises(IllegalAction):
        request(g, 1)
    assert g.snapshot() == snapshot
