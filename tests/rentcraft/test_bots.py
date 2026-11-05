"""Rule players receive only the public view and own their seat's decision memory."""
import json
import pytest

from boardcraft.games.rentcraft.actions import Phase
from boardcraft.registry import get_game
from boardcraft.inference.catalog import RuntimeBotCatalog
from .test_rules import act, table

NAMES = """Miser
Investor
Collector
Cautious
Master
Calculator
GPT-Style
Random
Absurd GPT-Style
Absurd Calculator
Absurd Investor
Hustler
Tactician
Simulator
Fast Simulator
Bonjour A
Bonjour B
Bonjour C
Resistant Bonjour A
Resistant Bonjour B
Resistant Bonjour C
Bonjour Calculator
Bonjour Calculator Pure
Bonjour Calculator Auctions
Bonjour Mixed Auctions
Resistant Bonjour B + Windows
Resistant Bonjour C + Windows
Wary Calculator
Absurd Resistant Bonjour B
Absurd Resistant Bonjour C
Absurd Bonjour Mixed Auctions
Absurd Wary Calculator
Absurd Tactician
Controller
Sol Sets""".splitlines()
KINDS = [name.lower().replace(" + ", "-plus-").replace(" ", "-") for name in NAMES]


def choose(bot, game):
    seat = game.decision().player
    return bot.choose(game.observe(seat), game.legal_actions(seat))


def purchase(cash, space=5):
    game = table(2, max_rounds=1)
    state = game.game.state
    state.phase, state.pending_buy = Phase.BUY_DECISION, space
    state.players[0].position, state.players[0].cash = space, cash
    return game


def test_exact_35_names_and_ids():
    catalog = RuntimeBotCatalog()
    assert {item.choice_id: item.display_name for item in catalog.list_bots("rentcraft")} == dict(zip(KINDS, NAMES))


@pytest.mark.parametrize("kind", KINDS)
def test_each_builtin_plays_through_public_adapter(kind):
    catalog = RuntimeBotCatalog()
    ref = catalog.pin_builtin("rentcraft", kind, {})
    first, twin = (catalog.resolve(ref, 42) for _ in range(2))
    game = table(2, max_rounds=1)
    before = twin.save_state()
    choice = choose(first, game)
    assert twin.save_state() == before
    assert choice == choose(twin, game)
    assert choice.explanation.strip()
    game.apply(0, choice.action, game.decision().id)
    # Exercise the next seats through the actual decisions caused by this move.
    seats = {0: first}
    for _ in range(3):
        decision = game.decision()
        if decision is None:
            break
        if decision.player not in seats:
            seats[decision.player] = catalog.resolve(ref, 42 + decision.player)
        follow = choose(seats[decision.player], game)
        assert follow.explanation.strip() and "fallback" not in follow.explanation.lower()
        game.apply(decision.player, follow.action, decision.id)


@pytest.mark.parametrize(("kind", "expected"), [
    ("bonjour-a", "decline"), ("bonjour-b", "buy"), ("bonjour-c", "buy"),
    ("resistant-bonjour-a", "decline"), ("resistant-bonjour-b", "buy"),
])
def test_station_priority_changes_purchase(kind, expected):
    assert choose(get_game("rentcraft").bot(kind, 10), purchase(245)).action.kind == expected


def test_bonjour_preserves_deed_priority_for_repayments_and_equal_rent_gains():
    game = table(2)
    state = game.game.state
    state.owner[31] = state.owner[37] = 0
    state.mortgaged[31] = state.mortgaged[37] = True
    assert move("bonjour-a", game).params == {"space": 37}
    for space in (16, 18, 19):
        state.owner[space] = 0
    state.buildings[19], state.bank_houses = 1, 31
    assert move("bonjour-a", game).params == {"space": 18}


@pytest.mark.parametrize(("kind", "want"), [("miser", "decline"), ("investor", "buy")])
def test_cash_reserve_changes_purchase(kind, want):
    assert choose(get_game("rentcraft").bot(kind, 2), purchase(250)).action.kind == want


@pytest.mark.parametrize("kind", ["controller", "sol-sets", "simulator", "fast-simulator", "hustler"])
def test_bot_memory_roundtrip(kind):
    module = get_game("rentcraft")
    bot, resumed = module.bot(kind, 11), module.bot(kind, 991)
    game = table(2, max_rounds=1)
    choose(bot, game)
    memory = json.loads(json.dumps(bot.save_state(), allow_nan=False))
    resumed.load_state(memory)
    assert resumed.save_state() == memory
    assert choose(bot, game) == choose(resumed, game)
    assert bot.save_state() == resumed.save_state()
    for bad in ({}, {**memory, "version": -1}, {**memory, "extra": 0}):
        with pytest.raises(ValueError):
            resumed.load_state(bad)


def test_simulator_ignores_hidden_deck_order():
    game = purchase(220)
    twin = game.clone()
    for order in twin.game.state.decks.values():
        order.reverse()
    bots = [get_game("rentcraft").bot("simulator", 17) for _ in range(2)]
    assert choose(bots[0], game) == choose(bots[1], twin)
    assert bots[0].save_state() == bots[1].save_state()


def test_hustler_requests_cash_for_nothing():
    game = table(2)
    choice = choose(get_game("rentcraft").bot("hustler", 3), game)
    assert choice.action.kind == "propose_trade"
    assert choice.action.params["get_cash"] > 0

    game.apply(0, choice.action, game.decision().id)


def move(kind, game, **options):
    return choose(get_game("rentcraft").bot(kind, 23, options=options), game).action


def test_master_preserves_cash_on_dark_blue_until_the_purchase_completes_a_set():
    game = purchase(1400, 39)
    assert move("master", game).kind == "decline"
    game.game.state.owner[37] = 0
    assert move("master", game).kind == "buy"


def test_resistant_and_tactician_refuse_a_sale_below_mortgage_value():
    game = table(2)
    game.game.state.owner[37] = 1
    act(game, "propose_trade", target=1, get_spaces=[37], give_cash=65)
    assert move("bonjour-a", game).kind == "accept_trade"
    for kind in ("resistant-bonjour-a", "tactician", "absurd-tactician"):
        assert move(kind, game).kind == "reject_trade"


def test_wary_calculator_prices_partial_progress_towards_a_set():
    game = table(3)
    game.game.state.owner[16] = 1
    act(game, "propose_trade", target=1, get_spaces=[16], give_cash=211)
    assert move("calculator", game).kind == "accept_trade"
    assert move("wary-calculator", game).kind in ("counter_trade", "reject_trade")


def test_controller_spends_its_reserve_to_block_a_rival_set():
    game = purchase(470, 39)
    game.game.state.owner[37] = 1
    assert move("resistant-bonjour-c-plus-windows", game).kind == "decline"
    assert move("controller", game).kind == "buy"


def test_auction_variants_use_different_limits_on_priority_stations():
    game = purchase(1010, 15)
    act(game, "decline")
    game.game.state.auction.high_bid = 270
    pure = move("calculator", game)
    mixed = move("resistant-bonjour-c", game)
    assert mixed != pure
    for kind in ("bonjour-calculator-pure", "bonjour-calculator-auctions"):
        assert move(kind, game) == pure
    for kind in ("bonjour-calculator", "bonjour-mixed-auctions", "absurd-bonjour-mixed-auctions"):
        assert move(kind, game) == mixed


def test_window_variants_buy_from_a_payer_before_rent_settles():
    from boardcraft.games.rentcraft.dice import FixedDice

    game = table(3, trade_windows="payments")
    state = game.game.state
    state.owner[8], state.owner[6], state.owner[9], state.owner[11] = 0, 1, 1, 2
    game.game.dice = FixedDice([(6, 5)])
    act(game, "roll")
    for base in ("resistant-bonjour-b", "resistant-bonjour-c"):
        assert move(base, game).kind == "pass"
        offer = move(base + "-plus-windows", game)
        assert offer.kind == "propose_trade" and offer.params["get_spaces"] == [8]


@pytest.mark.parametrize("kind", ["gpt-style", "controller"])
def test_set_seekers_propose_to_buy_the_last_street_and_resume(kind):
    game = table(4)
    state = game.game.state
    state.owner[16], state.owner[19], state.owner[18] = 0, 0, 1
    bot = get_game("rentcraft").bot(kind, 41)
    offer = choose(bot, game)
    assert offer.action.kind == "propose_trade" and offer.action.params["get_spaces"] == [18]
    game.apply(0, offer.action, game.decision().id)
    act(game, "reject_trade")
    saved = json.loads(json.dumps(bot.save_state()))
    resumed = get_game("rentcraft").bot(kind, 333)
    resumed.load_state(saved)
    assert choose(resumed, game) == choose(bot, game)
    assert resumed.save_state() == bot.save_state()


def test_sol_sets_negotiation_memory_survives_refusals_and_changes_the_next_offer():
    game = table(4)
    state = game.game.state
    state.owner[16], state.owner[19], state.owner[18] = 0, 0, 1
    bot = get_game("rentcraft").bot("sol-sets", 41)
    offered = set()
    for turn in range(5):
        state.turn = 1 + 4 * turn
        state.trades_this_turn = 0
        resumed = get_game("rentcraft").bot("sol-sets", 22)
        resumed.load_state(json.loads(json.dumps(bot.save_state())))
        choice = choose(resumed, game)
        assert choice == choose(bot, game)
        assert resumed.save_state() == bot.save_state()
        assert choice.action.kind == "propose_trade"
        signature = json.dumps(choice.action.params, sort_keys=True)
        assert signature not in offered
        offered.add(signature)
        if choice.action.params["get_spaces"] == [18]:
            break
        game.apply(0, choice.action, game.decision().id)
        act(game, "reject_trade")
    else:
        pytest.fail("Sol Sets did not negotiate for the missing street")


def test_simulation_budgets_and_clear_purchase_shortcut():
    from boardcraft.games.rentcraft.bots.fast_simulator import BATCH, FastSimulatorBot
    from boardcraft.games.rentcraft.bots.simulator import MAX_PROPOSALS, SimulatorBot

    assert BATCH == 4 and MAX_PROPOSALS == 3
    for strategy in (SimulatorBot(), FastSimulatorBot()):
        assert (strategy.samples, strategy.horizon) == (12, 25)
    bot = get_game("rentcraft").bot("fast-simulator", 31)
    before = bot.save_state()["memory"]["fields"]["_rng"]
    assert choose(bot, purchase(1300, 5)).action.kind == "buy"
    assert bot.save_state()["memory"]["fields"]["_rng"] == before


@pytest.mark.parametrize("kind", ["simulator", "fast-simulator"])
def test_rollout_explanation_describes_only_the_chosen_option(kind):
    result = choose(get_game("rentcraft").bot(kind, 38), purchase(220))
    other = "Decline" if result.action.kind == "buy" else "Buy"
    assert other not in result.explanation


def test_random_explores_both_purchase_choices_and_risk_settings_change_behavior():
    game = purchase(410, 39)
    varied = get_game("rentcraft").bot("random", 909)
    assert {choose(varied, game).action.kind for _ in range(20)} == {"buy", "decline"}
    early = purchase(290, 6)
    assert move("gpt-style", early, starter_left=20).kind == "buy"
    assert move("gpt-style", early, starter_left=500, min_reserve=400).kind == "decline"


def test_rollout_retains_publicly_known_card_returns():
    from random import Random
    from boardcraft.games.rentcraft.bots.simulator import rollout
    from boardcraft.games.rentcraft.actions import Action, ActionKind
    from boardcraft.games.rentcraft.observation import hypothetical_game

    # Every card has circulated publicly. Land on Opportunity, whose known top card pays $50.
    game = table(2, max_rounds=1)
    world = hypothetical_game(game.observe(0), 19)
    state = world.state
    for name, order in state.decks.items():
        state.memory.known_bottom[name] = list(order)
    opportunity = state.memory.known_bottom["opportunity"]
    opportunity.remove(7)
    opportunity.insert(0, 7)
    state.decks["opportunity"] = list(opportunity)
    rng = Random(12)
    steps = rng.randint(1, 6) + rng.randint(1, 6)
    state.players[0].position = (22 - steps) % 40
    value = rollout(state, 0, Action(ActionKind.ROLL), 12, horizon=0)
    assert value == 1550 / 3050


def test_checkpoint_validates_nested_memory_atomically():
    import copy
    game = table(2)
    bot = get_game("rentcraft").bot("controller", 7)
    choose(bot, game)
    before = bot.save_state()
    for corrupt in (
        lambda s: s["memory"]["fields"].update(_sent=[False]),
        lambda s: s["memory"]["delegates"].clear(),
        lambda s: s["memory"]["delegates"][0]["fields"].update(_win_steps=[]),
        lambda s: s["world_rng"][1].__setitem__(23, True),
    ):
        broken = copy.deepcopy(before)
        corrupt(broken)
        with pytest.raises(ValueError):
            bot.load_state(broken)
        assert bot.save_state() == before


def test_sol_plans_keep_other_pending_transfers_separate_across_tables():
    from boardcraft.games.rentcraft.bots.sol_sets_plans import apply_build
    from boardcraft.games.rentcraft.bots.sol_sets_evaluation import Snap
    game = table(3)
    state = game.game.state
    state.owner[1], state.owner[3], state.owner[5] = 0, 0, 0
    state.mortgaged[5] = True
    base = Snap.from_state(state)
    pending = base.copy()
    pending.fresh = frozenset({5})
    assert apply_build(base, "brown", (1, 1)).fresh == frozenset()
    assert apply_build(pending, "brown", (1, 1)).fresh == frozenset({5})


def test_sol_trade_search_uses_the_remaining_endgame_rounds():
    from boardcraft.games.rentcraft.bots.sol_sets import SolSetsBot
    from boardcraft.games.rentcraft.bots.sol_sets_evaluation import Ctx, Snap
    game = table(2, max_rounds=2)
    state = game.game.state
    bot = SolSetsBot()
    snap = Snap.from_state(state)
    bot._search(Ctx(state), snap, 0, True)
    state.round += 1
    assert bot._search(Ctx(state), snap, 0, True).ctx.rounds_left == 1


def test_review_zero_income_horizon_preserves_sol_sets_valuation():
    game = purchase(1500, 5)
    choice = choose(get_game("rentcraft").bot("sol-sets", 7, options={"income_horizon": 0}), game)
    assert choice.action.kind == "buy"
    assert "fallback" not in choice.explanation.lower()


def test_review_negative_attempt_counter_is_rejected_before_installation():
    game = table(4)
    state = game.game.state
    state.owner[16], state.owner[19], state.owner[18] = 0, 0, 1
    bot = get_game("rentcraft").bot("gpt-style", 15)
    control = get_game("rentcraft").bot("gpt-style", 15)
    before = bot.save_state()
    damaged = json.loads(json.dumps(before))
    damaged["memory"]["fields"]["_attempts"] = [[[1, 18], -7]]
    with pytest.raises(ValueError):
        bot.load_state(damaged)
    assert bot.save_state() == before
    result = choose(bot, game)
    assert result.action.kind == "propose_trade" and result == choose(control, game)
    assert bot.save_state() == control.save_state()


@pytest.mark.parametrize("kind", ["gpt-style", "tactician", "absurd-gpt-style", "absurd-tactician"])
def test_review_fallback_explanation_omits_internal_errors(kind):
    game = table(4)
    state = game.game.state
    state.owner[16], state.owner[19], state.owner[18] = 0, 0, 1
    bot = get_game("rentcraft").bot(kind, 17, options={"price_steps": [1e308]})
    result = choose(bot, game)
    assert result.action in game.legal_actions(0)
    assert result.explanation == "Use the available fallback move."


@pytest.mark.parametrize("reply", [False, True])
def test_review_sol_sets_explanation_only_prices_the_submitted_offer(reply):
    import re
    game = table(4)
    state = game.game.state
    buyer = int(reply)
    state.owner[16] = state.owner[19] = buyer
    state.owner[18] = 1 - buyer
    if reply:
        act(game, "propose_trade", target=1, give_spaces=[18], get_cash=850)
    result = choose(get_game("rentcraft").bot("sol-sets", 41), game)
    assert result.action.kind == ("counter_trade" if reply else "propose_trade")
    money = [int(v) for v in re.findall(r"\$(\d+)", result.explanation)]
    params = result.action.params
    assert money == [v for v in (params["give_cash"], params["get_cash"]) if v]


def test_review_counter_explains_its_final_adjustment_after_variation():
    game = table(3)
    state = game.game.state
    state.owner[6], state.owner[8], state.owner[9] = 1, 0, 0
    act(game, "propose_trade", target=1, get_spaces=[6], give_cash=215)
    result = choose(get_game("rentcraft").bot("calculator", 1, options={"trade_variation": 1}), game)
    assert result.action.kind == "counter_trade"
    assert result.action.params["get_cash"] == 420
    assert "$205 more" in result.explanation

