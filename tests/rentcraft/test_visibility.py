import json

from boardcraft.games.rentcraft.dice import FixedDice
from .test_rules import act, table


def test_hidden_order_independence():
    g = table()
    twin = g.clone()
    twin.game.state.decks["opportunity"].reverse()
    for viewer in (None, 0, 1, 2):
        assert g.observe(viewer) == twin.observe(viewer)
        assert g.command_specs(viewer) == twin.command_specs(viewer)
        view = g.observe(viewer)
        for hidden in ("decks", "seed", "rng", "draft"):
            assert f'"{hidden}"' not in json.dumps(view.data)
    assert not g.command_specs(None)
    assert {s["kind"] for s in g.command_specs(1)} == {"request_trade", "resign"}
    assert "propose_trade" in {s["kind"] for s in g.command_specs(0)}
    view = g.observe(0)
    view.data["players"][0]["cash"] = 2
    assert g.observe(0).data["players"][0]["cash"] == 1500


def test_model_context_exposes_only_own_actions_and_own_detached_snapshot():
    from boardcraft.contracts import GameConfig
    from boardcraft.games.rentcraft.module import RentcraftModule
    from boardcraft.games.rentcraft.observation import hypothetical_game, require_model_context
    import pytest

    g = RentcraftModule().new(GameConfig("rentcraft", "base", 3, {"trade_windows": "off"}), 129)
    seat = g.decision().player
    act(g, "propose_trade", target=(seat + 1) % 3, give_cash=17)
    own = g.observe(seat).data["model_context"]
    assert len(own["own_acts"]) == 1
    for viewer in (None, (seat + 1) % 3, (seat + 2) % 3):
        assert g.observe(viewer).data["model_context"]["own_acts"] == []
    assert "model_history" not in g.observe(None).data["memory"]
    assert not {"origin", "steps", "draft", "draft_sent"} & own["own_acts"][0].keys()
    twin = g.clone()
    twin.game.state.decks["opportunity"].reverse()
    assert twin.observe(seat) == g.observe(seat)
    own["own_acts"][0]["amount"] = 999
    assert g.observe(seat).data["model_context"]["own_acts"][0]["amount"] == 17
    with pytest.raises(ValueError, match="seat"):
        require_model_context(g.observe(None))
    world = hypothetical_game(g.observe(seat), 11)
    assert world.state.memory.model_history is None
    assert world.decision() == g.game.decision()


def test_fork_future_only():
    g = table()
    s = g.game.state
    deck = s.decks["opportunity"]
    deck.remove(7)
    deck.insert(0, 7)
    g.game.dice = FixedDice([(3, 4)])
    act(g, "roll")
    assert s.players[0].cash == 1550
    other = g.fork(42)
    assert other.decision() == g.decision() and other.observe(None) == g.observe(None)
    assert other.game.state.decks["opportunity"] != deck
    assert other.game.state.decks["opportunity"][-1] == deck[-1] == 7
    assert g.fork(42).snapshot() == other.snapshot()


def test_public_offers_and_detached_receipts():
    g = table()
    events = act(g, "propose_trade", target=1, give_cash=13)
    public = g.project_events(events, None)
    assert public[0].payload["offer"]["give_cash"] == 13
    public[0].payload["offer"]["give_cash"] = 1
    assert events[0].payload["offer"]["give_cash"] == 13
    assert g.observe(2).data["trade"]["history"][0]["give_cash"] == 13



def test_simulations_are_built_entirely_from_public_information():
    from boardcraft.games.rentcraft.observation import hypothetical_game
    from boardcraft.games.rentcraft.persistence import plain

    g = table()
    act(g, "propose_trade", target=1, give_cash=17)
    twin = g.clone()
    twin.game.state.decks["community_fund"].reverse()
    world = hypothetical_game(g.observe(1), 111)
    other = hypothetical_game(twin.observe(1), 111)
    assert plain(world.state) == plain(other.state)
    assert world.state.decks != g.game.state.decks
    assert world.state.trade.offer.target == 1 and world.state.config.seed == 111
    assert world.decision() == g.game.decision()
    world.state.players[0].cash = 1
    assert g.game.state.players[0].cash == 1500


def test_release_card_stays_out_of_fork_until_returned():
    from boardcraft.games.rentcraft.actions import Phase
    from boardcraft.games.rentcraft.module import RentcraftModule

    g = table()
    s = g.game.state
    order = s.decks["opportunity"]
    order.remove(8)
    order.insert(0, 8)
    g.game.dice = FixedDice([(3, 4)])
    act(g, "roll")
    assert g.fork(201).game.state.players[0].jail_cards == ["opportunity"]
    assert 8 not in g.fork(201).game.state.decks["opportunity"]
    s.players[0].position, s.players[0].in_jail, s.phase = 10, True, Phase.PRE_ROLL
    act(g, "use_jail_card")
    assert g.observe(None).data["memory"]["known_bottom"]["opportunity"] == [8]
    assert g.fork(202).game.state.decks["opportunity"][-1] == 8
    assert RentcraftModule().restore(g.snapshot()).snapshot() == g.snapshot()


def test_receipts_survive_rejection_and_reject_fabricated_events():
    import pytest
    from boardcraft.contracts import Action, Event, IllegalAction

    g = table()
    events = act(g, "propose_trade", target=1, give_cash=1)
    with pytest.raises(IllegalAction):
        g.apply(1, Action("counter_trade", "counter_trade", {"target": 2}), g.decision().id)
    assert g.project_events(events, None) == events
    with pytest.raises(ValueError):
        g.project_events((Event(1, "private", {"deck": [1]}),), None)


def test_review_restored_receipts_have_typed_public_payloads():
    import copy
    import pytest
    from boardcraft.games.rentcraft.module import RentcraftModule

    g = table()
    base = g.snapshot()
    resumed = RentcraftModule().restore(base)
    public = resumed.project_events(resumed.initial_events(), None)
    assert public[0].payload["dice"] == base["bootstrap"][0]["payload"]["dice"]
    mutations = [
        lambda p: p.update(dice={"order": base["state"]["decks"]["opportunity"]}),
        lambda p: p.update(dice=[6, True]), lambda p: p.update(dice=[0, 1]), lambda p: p.pop("dice"),
        lambda p: p.update(tiebreak={}), lambda p: p.pop("tiebreak"),
    ]
    for mutate in mutations:
        snap = copy.deepcopy(base)
        for key in ("receipt", "bootstrap"):
            mutate(snap[key][0]["payload"])
        with pytest.raises(ValueError):
            RentcraftModule().restore(snap)


def test_review_restored_group_sale_and_creditor_shares_are_closed_shapes():
    import copy
    import pytest
    from boardcraft.contracts import Action
    from boardcraft.games.rentcraft.actions import Phase
    from boardcraft.games.rentcraft.module import RentcraftModule
    from boardcraft.games.rentcraft.state import Debt

    game = table()
    state = game.game.state
    for space, owner, level in [(1, 0, 5), (3, 0, 5)] + [(s, 1, 4) for s in (6, 8, 9, 11, 13, 14, 37, 39)]:
        state.owner[space], state.buildings[space] = owner, level
    state.bank_houses, state.bank_hotels = 0, 10
    sale = act(game, "sell_building", space=1)
    before = game.snapshot()
    assert RentcraftModule().restore(before).project_events(sale, None) == sale
    damaged = copy.deepcopy(before)
    damaged["receipt"][0]["payload"]["levels"][0] = {"cards": [1, 2]}
    with pytest.raises(ValueError):
        RentcraftModule().restore(damaged)

    game = table()
    state = game.game.state
    state.phase, state.debts, state.players[0].cash = Phase.RAISE_FUNDS, [Debt(0, 1, 10), Debt(0, 2, 10)], 5
    payments = game.apply_seat_operation(0, Action("resign", "resign", {}), 0)
    before = game.snapshot()
    assert RentcraftModule().restore(before).project_events(payments, None) == payments
    for field, value in [("seat", False), ("amount", []), ("hidden", [1, 2])]:
        damaged = copy.deepcopy(before)
        entry = next(e for e in damaged["receipt"] if e["kind"] == "bankrupt")
        entry["payload"]["creditors"][0][field] = value
        with pytest.raises(ValueError):
            RentcraftModule().restore(damaged)
