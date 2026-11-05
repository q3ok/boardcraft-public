"""All 221 mask bits and decision-local draft transitions."""
import copy

import numpy as np
import pytest

from boardcraft.games.rentcraft.actions import Action, ActionKind as K, Phase
from boardcraft.games.rentcraft.state import AuctionState, Debt, TradeWindow
from boardcraft.games.rentcraft.trade import TradeOffer, TradeState
from .test_model_encoders import MINI, V7, decision_game, fresh, profile


@pytest.mark.parametrize("n", [2, 3, 4])
@pytest.mark.parametrize("phase,ids", [
    (Phase.PRE_ROLL, [0]), (Phase.POST_ROLL, [1]), (Phase.BUY_DECISION, [2,3]),
    (Phase.AUCTION, [4,5,6,7,8]), (Phase.RAISE_FUNDS, [3]),
    (Phase.TRADE_RESPONSE, [11,12]), (Phase.TRADE_WINDOW, [1]),
    (Phase.MORTGAGE_TAKEOVER, [1,134]), (Phase.GAME_OVER, []),
])
def test_full_221_masks_in_every_phase(n, phase, ids):
    game = fresh(n)
    s, p = game.state, game.state.current
    s.phase = phase
    s.config.max_trade_exchanges = 0
    if phase is Phase.BUY_DECISION:
        s.pending_buy = 1
    if phase is Phase.AUCTION:
        s.auction = AuctionState(1, [p, (p+1)%n])
        s.players[p].cash = 0  # bidding above cash remains legal
    if phase is Phase.RAISE_FUNDS:
        s.players[p].cash = 0
        s.debts = [Debt(p, None, 100)]
    if phase is Phase.TRADE_RESPONSE:
        s.trade = TradeState(TradeOffer((p+1)%n, p, give_cash=25), Phase.PRE_ROLL)
    if phase is Phase.TRADE_WINDOW:
        s.window = TradeWindow("request", None, [p], "restore", Phase.PRE_ROLL, None, 1)
    if phase is Phase.MORTGAGE_TAKEOVER:
        s.owner[1], s.mortgaged[1] = p, True
        s.pending_takeovers = [(p, 1)]
    expected = np.zeros(221, bool)
    expected[ids] = True
    for name in (MINI, V7):
        space = profile(name).action_space()
        assert space.n == 221
        np.testing.assert_array_equal(space.mask(decision_game(game)), expected)
        for i in ids:
            a = space.to_action(decision_game(game), i)
            assert a in game.legal_actions()


def test_fixed_numbering_auctions_and_phase_aliases():
    space = profile(V7).action_space()
    assert [space.actions[i].amount for i in range(4,8)] == [1,10,50,100]
    assert (space.propose0, space.counter_index, space.empty0, space.counter_received, space.toggle0,
            space.cash0, space.clear_cash, space.card_give, space.card_get, space.send, space.cancel) == (
        173,177,178,181,182,210,216,217,218,219,220)
    game = fresh()
    game.state.phase = Phase.AUCTION
    game.state.auction = AuctionState(1, [game.state.current], high_bid=9990)
    mask = space.mask(decision_game(game))
    np.testing.assert_array_equal(mask[4:9], [True,True,False,False,True])


def test_reversals_debt_exception_and_takeover_payoff_exception():
    game = fresh()
    s, p = game.state, game.state.current
    s.owner[1], s.mortgaged[1] = p, True
    s.memory.changed_turn, s.memory.changed = s.turn, {1:-1}
    space = profile(V7).action_space()
    assert not space.mask(decision_game(game))[134]
    s.phase, s.debts = Phase.RAISE_FUNDS, [Debt(p, None, 500)]
    s.mortgaged[1] = False
    s.memory.changed[1] = 1
    assert space.mask(decision_game(game))[94]
    s.phase, s.debts, s.pending_takeovers = Phase.MORTGAGE_TAKEOVER, [], [(p,1)]
    s.mortgaged[1], s.memory.changed[1] = True, -1
    assert space.mask(decision_game(game))[134]


def test_three_opens_reset_at_next_decision_and_sixteen_edits_cancel_only():
    game = fresh()
    space, dg = profile(V7).action_space(), decision_game(game)
    for _ in range(3):
        assert space.mask(dg)[178]
        assert space.to_action(dg,178) is None
        assert space.to_action(dg,220) is None
    assert not space.mask(dg)[173:182].any()
    other = decision_game(game, dg.overlay)
    assert other.trade_draft is None and other.trade_draft_opens is None
    assert space.mask(other)[178]
    space.to_action(other,178)
    for i in range(16):
        space.to_action(other,210 if i%2 == 0 else 216)
    expected = np.zeros(221,bool)
    expected[220] = True
    np.testing.assert_array_equal(space.mask(other), expected)


def test_cash_cards_send_cancel_and_repeated_offer():
    game = fresh()
    s, p = game.state, game.state.current
    r = (p+1)%3
    s.players[p].cash = 25
    s.players[r].cash = 0
    s.owner[1] = r  # recipient may raise 30 at the bank
    s.players[p].jail_cards = ["opportunity"]
    space, dg = profile(V7).action_space(), decision_game(game)
    before = copy.deepcopy(s.memory)
    space.to_action(dg,178)
    mask = space.mask(dg)
    np.testing.assert_array_equal(mask[210:219], [1,0,0,1,0,0,0,1,0])
    assert not mask[219] and mask[220]
    space.to_action(dg,210)
    space.to_action(dg,217)
    assert space.mask(dg)[219]
    action = space.to_action(dg,219)
    assert action == Action(K.PROPOSE_TRADE, trade=TradeOffer(p,r,give_cash=25,give_cards=1))
    assert s.memory == before  # private notes never touch engine memory
    game.apply(action)
    game.apply(Action(K.REJECT_TRADE))
    again = decision_game(game,dg.overlay)
    space.to_action(again,178)
    space.to_action(again,210)
    space.to_action(again,217)
    assert not space.mask(again)[219]


def test_sixty_four_calls_error_and_each_step_has_new_encoding():
    from boardcraft.games.rentcraft.inference.tradebuilder import choose_index
    game = fresh()
    space, dg, seen = profile(V7).action_space(), decision_game(game), []
    enc = profile(V7).encoder()
    def pick(mask):
        seen.append(enc.encode(dg.state, game.state.current, dg))
        return 178 if len(seen)==1 else 210
    with pytest.raises(RuntimeError, match="finish"):
        choose_index(space,dg,pick)
    assert len(seen)==64 and dg.trade_draft is None
    assert not np.array_equal(seen[0],seen[1])
    assert not np.array_equal(seen[1],seen[2])


@pytest.mark.parametrize("delta,origin", [(0,"empty"),(24,"empty"),(-24,"empty"),(25,"")])
def test_overlay_open_cancel_open_send_then_next_decision_same_turn(delta, origin):
    from boardcraft.games.rentcraft.inference.tradebuilder import NoteOverlay
    game = fresh()
    s, p = game.state, game.state.current
    space, overlay = profile(V7).action_space(), NoteOverlay(p)
    dg = decision_game(game, overlay)
    space.to_action(dg,178)
    space.to_action(dg,210)
    space.to_action(dg,220)
    space.to_action(dg,178)
    space.to_action(dg,211)
    action = space.to_action(dg,219)
    if delta:
        action = Action(K.PROPOSE_TRADE,trade=TradeOffer(p,(p+1)%3,give_cash=100+delta))
    game.apply(action)
    game.apply(Action(K.REJECT_TRADE))
    next_decision = decision_game(game,overlay)
    assert s.turn == 1 and next_decision.trade_draft is None and next_decision.trade_draft_opens is None
    rows = next_decision.state.memory.model_acts
    assert [(a.kind,a.origin,a.steps,a.outcome) for a in rows] == [
        ("abandoned_draft","empty",1,""),("offer",origin,1 if origin else 0,"rejected")]
    assert [(a.amount,a.phase,a.window,a.trigger) for a in rows] == [
        (25,"pre_roll","",None),(100+delta,"pre_roll","",None)]
    v = profile(V7).encoder().encode(next_decision.state,p,next_decision)
    assert v[594+14] == np.float32(1/3)
    assert not v[695:847].any()
    assert v[3413+8] == np.float32(np.log1p(1/5))
    assert v[3413+14] == np.float32(np.log1p(1/5))
    np.testing.assert_array_equal(v[3430:3444], [9,2,0,0,0,0,0,0,0,2,3 if origin else 0,1,0,0])
    assert v[3430+18] == (1/16 if origin else 0)
    assert v[3430+19] == 15
    # The overlay is per seat, the public accepted action has no private fields.
    assert not hasattr(s.memory.model_history.own_acts[0],"origin")
    isolated = decision_game(game,NoteOverlay(p))
    assert len(isolated.state.memory.model_acts)==1 and isolated.state.memory.model_acts[0].origin==""
    s.turn += 1
    cleared = decision_game(game,overlay)
    assert cleared.state.memory.model_acts==[]


def test_overlay_retains_source_limit_and_preserves_outcome_updates():
    from boardcraft.games.rentcraft.inference.tradebuilder import NoteOverlay
    game = fresh()
    p, space = game.state.current, profile(V7).action_space()
    overlay = NoteOverlay(p)
    for _ in range(24):
        dg = decision_game(game,overlay)
        for _ in range(3):
            space.to_action(dg,178)
            space.to_action(dg,220)
    assert len(dg.state.memory.model_acts)==64
    assert len(decision_game(game,overlay).state.memory.model_acts)==64


def test_overlay_identical_accepted_rows_at_retention_boundary_are_new_actions():
    from boardcraft.games.rentcraft.inference.tradebuilder import NoteOverlay
    game = fresh()
    s,p = game.state,game.state.current
    space,overlay = profile(V7).action_space(),NoteOverlay(p)
    for i in range(70):
        s.phase = Phase.TRADE_WINDOW
        s.window = TradeWindow("request",None,[p],"restore",Phase.PRE_ROLL,None,i+1)
        dg = decision_game(game,overlay)
        space.to_action(dg,178)
        space.to_action(dg,220)
        game.apply(space.to_action(dg,1))
    s.phase = Phase.TRADE_WINDOW
    s.window = TradeWindow("request",None,[p],"restore",Phase.PRE_ROLL,None,71)
    rows = decision_game(game,overlay).state.memory.model_acts
    assert len(rows)==64
    assert [a.kind for a in rows] == ["abandoned_draft","pass_window"]*32


def test_overlay_observation_revision_disambiguates_repeated_rows_without_duplicate_reads():
    from boardcraft.games.rentcraft.inference.tradebuilder import DecisionGame,NoteOverlay
    from .test_model_history import fresh as public_fresh
    from .test_rules import act
    game = public_fresh()
    s,p = game.game.state,game.game.state.current
    space,overlay = profile(V7).action_space(),NoteOverlay(p)
    for i in range(70):
        s.phase = Phase.TRADE_WINDOW
        s.window = TradeWindow("request",None,[p],"restore",Phase.PRE_ROLL,None,i+1)
        obs = game.observe(p)
        dg = DecisionGame.from_observation(obs,overlay)
        space.to_action(dg,178)
        space.to_action(dg,220)
        space.to_action(dg,1)
        # Repeated reads before the final command is accepted append no actions.
        repeated = DecisionGame.from_observation(obs,overlay)
        assert len(repeated.overlay.acts)==len(dg.overlay.acts)
        act(game,"pass")
    s.phase = Phase.TRADE_WINDOW
    s.window = TradeWindow("request",None,[p],"restore",Phase.PRE_ROLL,None,71)
    rows = DecisionGame.from_observation(game.observe(p),overlay).overlay.acts
    assert [a.kind for a in rows]==["abandoned_draft","pass_window"]*32
