import pytest

from boardcraft.contracts import Action, GameConfig
from boardcraft.games.rentcraft.dice import FixedDice
from boardcraft.games.rentcraft.module import RentcraftModule
from boardcraft.games.rentcraft.state import Debt
from boardcraft.games.rentcraft.actions import Phase


def table(n=3, **settings):
    config = GameConfig("rentcraft", "base", n, {"trade_windows": "off", **settings})
    game = RentcraftModule().new(config, 129)
    game.game.state.current = game.game.state.first_player = 0
    return game


def act(game, kind, **params):
    decision = game.decision()
    if kind in ("counter_trade", "propose_trade"):
        action = Action(kind, kind, params)
    else:
        action = next(a for a in game.legal_actions(decision.player)
                      if a.kind == kind and a.params == params)
    return game.apply(decision.player, action, decision.id)


@pytest.mark.parametrize("seats", [2, 3, 4])
def test_setup(seats):
    g = RentcraftModule().new(GameConfig("rentcraft", "base", seats), 12)
    state = g.game.state
    assert [p.cash for p in state.players] == [1500] * seats
    assert (state.bank_houses, state.bank_hotels) == (32, 12)
    assert state.config.trade_windows == "full"
    assert state.config.max_rounds is None
    events = g.initial_events()
    assert events[-1].kind == "start_player"
    assert events[-1].payload["player"] == g.decision().player
    assert len([e for e in events if e.kind == "start_roll"]) >= seats


@pytest.mark.parametrize("settings", [{"start_roll": False}, {"rules": 3}, {"max_rounds": 0},
                                     {"max_trades_per_turn": -1}, {"trade_windows": "unknown"},
                                     {"start_cash": -1}, {"max_trade_exchanges": -1}])
def test_invalid_settings(settings):
    with pytest.raises(ValueError):
        table(**settings)


def test_doubles_and_jail():
    g = table()
    s = g.game.state
    s.players[0].position = 18
    g.game.dice = FixedDice([(1, 1), (5, 5), (2, 3)])
    act(g, "roll")
    assert s.players[0].position == 20 and s.extra_roll
    act(g, "roll")
    assert s.players[0].in_jail and s.players[0].position == 10
    s.phase, s.players[0].jail_turns = Phase.PRE_ROLL, 2
    act(g, "roll")
    assert s.players[0].cash == 1450 and s.players[0].position == 15


def test_third_double_without_moving():
    g = table()
    s = g.game.state
    s.doubles = 2
    g.game.dice = FixedDice([(6, 6)])
    act(g, "roll")
    assert s.players[0].in_jail and s.players[0].position == 10


def test_even_building_sale_and_mortgage():
    g = table()
    s = g.game.state
    s.owner[1] = s.owner[3] = 0
    act(g, "build", space=1)
    actions = g.legal_actions(0)
    assert not any(a.kind == "mortgage" or a.kind == "build" and a.params["space"] == 1 for a in actions)
    act(g, "build", space=3)
    act(g, "build", space=1)
    assert not any(a.kind == "sell_building" and a.params["space"] == 3 for a in g.legal_actions(0))
    act(g, "sell_building", space=1)
    act(g, "sell_building", space=3)
    act(g, "sell_building", space=1)
    act(g, "mortgage", space=1)
    assert s.players[0].cash == 1455 and s.bank_houses == 32
    act(g, "unmortgage", space=1)
    assert s.players[0].cash == 1422


def test_rent_schedule():
    from boardcraft.games.rentcraft.rent import rent_for

    s = table().game.state
    s.owner[1] = s.owner[3] = 1
    assert rent_for(s, 1, 7) == 4
    s.buildings[1] = 3
    assert rent_for(s, 1, 7) == 90
    for i in (5, 15, 25, 35, 12, 28):
        s.owner[i] = 1
    assert rent_for(s, 15, 7) == 200
    assert rent_for(s, 12, 7) == 70
    s.mortgaged[15] = True
    assert rent_for(s, 15, 7) == 0


def test_auction_steps_limit_and_debt():
    g = table(2, start_cash=0)
    s = g.game.state
    s.phase, s.pending_buy = Phase.BUY_DECISION, 1
    act(g, "decline")
    assert [a.params["amount"] for a in g.legal_actions(1) if a.kind == "bid"] == [1, 10, 50, 100]
    s.auction.high_bid = 9999
    act(g, "bid", amount=1)
    assert [a.kind for a in g.legal_actions(0)] == ["pass_bid"]
    act(g, "pass_bid")
    assert s.phase == Phase.RAISE_FUNDS and s.debts == [Debt(1, None, 10000)]
    assert s.owner[1] == 1


def test_resign_settles_debts():
    g = table()
    s = g.game.state
    s.phase, s.debts = Phase.RAISE_FUNDS, [Debt(0, 1, 40), Debt(0, 2, 20)]
    s.players[0].cash, s.owner[5] = 5, 0
    events = g.apply_seat_operation(0, Action("resign", "resign", {}), 0)
    assert [e.kind for e in events][:5] == ["resign", "mortgage", "pay", "pay", "bankrupt"]
    assert [p.cash for p in s.players] == [0, 1540, 1520]


def test_multiple_creditors_split_in_turn_order():
    g = table()
    s = g.game.state
    s.phase, s.debts = Phase.RAISE_FUNDS, [Debt(0, 1, 10), Debt(0, 2, 10)]
    s.players[0].cash = 5
    act(g, "declare_bankruptcy")
    assert [p.cash for p in s.players] == [0, 1503, 1502]



def test_precedence_tied_leaders_reroll():
    from boardcraft.games.rentcraft.engine import Game
    from boardcraft.games.rentcraft.state import GameConfig as Rules

    g = Game.new(Rules(3, 42), FixedDice([(5, 5), (4, 6), (1, 1), (1, 5), (4, 4)]))
    assert g.state.first_player == 1
    assert [(p, attempt) for p, _, attempt in g.state.start_rolls] == [(0, 1), (1, 1), (2, 1), (0, 2), (1, 2)]


@pytest.mark.parametrize("operation,cash,mortgaged", [("pass", 1492, True), ("unmortgage", 1417, False)])
def test_mortgage_takeover_rounded_interest(operation, cash, mortgaged):
    g = table()
    s = g.game.state
    s.owner[12], s.mortgaged[12] = 0, True
    act(g, "propose_trade", target=1, give_spaces=[12])
    act(g, "accept_trade")
    assert g.decision().kind == "mortgage_takeover"
    act(g, operation, **({"space": 12} if operation == "unmortgage" else {}))
    assert s.players[1].cash == cash and s.mortgaged[12] is mortgaged


def test_hotel_shortage_sells_the_whole_group_evenly():
    g = table()
    s = g.game.state
    for i in (1, 3):
        s.owner[i], s.buildings[i] = 0, 5
    for i in (6, 8, 9, 11, 13, 14, 37, 39):
        s.owner[i], s.buildings[i] = 1, 4
    s.bank_houses, s.bank_hotels = 0, 10
    act(g, "sell_building", space=1)
    assert [s.buildings[i] for i in (1, 3)] == [0, 0]
    assert s.bank_hotels == 12 and s.players[0].cash == 1750


@pytest.mark.parametrize("deck,index,position,cash", [
    ("opportunity", i, pos, cash) for i, (pos, cash) in enumerate([
        (39, 1500), (0, 1700), (24, 1500), (11, 1500), (15, 1450), (15, 1450), (12, 1420),
        (7, 1550), (7, 1500), (4, 1300), (10, 1500), (7, 1250), (7, 1485), (5, 1700),
        (7, 1400), (7, 1650)])
] + [
    ("community_fund", i, pos, cash) for i, (pos, cash) in enumerate([
        (0, 1700), (2, 1700), (2, 1450), (2, 1550), (2, 1500), (10, 1500), (2, 1600),
        (2, 1520), (2, 1520), (2, 1600), (2, 1400), (2, 1450), (2, 1525), (2, 1190),
        (2, 1510), (2, 1600)])
])
def test_each_revealed_card_resolves_its_printed_effect(deck, index, position, cash):
    g = table()
    s = g.game.state
    # Two hotels on the brown group and two houses on the dark blue group.
    for sp, level in [(1, 5), (3, 5), (37, 1), (39, 1)]:
        s.owner[sp], s.buildings[sp] = 0, level
    s.bank_houses, s.bank_hotels = 30, 10
    s.owner[15] = s.owner[12] = 1
    s.decks[deck].remove(index)
    s.decks[deck].insert(0, index)
    g.game.dice = FixedDice([(3, 4) if deck == "opportunity" else (1, 1), (3, 5)])
    events = act(g, "roll")
    card = next(e for e in events if e.kind == "card")
    assert (card.payload["deck"], card.payload["card"]) == (deck, index)
    assert (s.players[0].position, s.players[0].cash) == (position, cash)
    if (deck, index) in (("opportunity", 8), ("community_fund", 4)):
        assert s.players[0].jail_cards == [deck] and index not in s.decks[deck]
    else:
        assert s.decks[deck][-1] == index
