"""Frozen model input contracts, using synthetic public positions only."""
import copy
import math

import numpy as np
import pytest

from boardcraft.games.rentcraft.actions import Action, ActionKind, Phase
from boardcraft.games.rentcraft.board import BUYABLE
from boardcraft.games.rentcraft.engine import Game
from boardcraft.games.rentcraft.memory import LogEntry, ModelAct, PairOffer, model_snapshot
from boardcraft.games.rentcraft.state import GameConfig, AuctionState, Debt, TradeWindow

MINI = "rentcraft-legacy-blocks-mini-evo5"
V7 = "rentcraft-visible-v7-offers8"
MINI_LAYOUT = dict(zip(
    ("state", "trade_candidates", "context", "memory", "rents", "complete_sets", "debts",
     "trade_history", "liquidity", "draft", "cards", "changes"),
    ((0, 516), (516, 45), (561, 22), (583, 142), (725, 142), (867, 32), (899, 4),
     (903, 24), (927, 9), (936, 71), (1007, 20), (1027, 28))))
V7_LAYOUT = dict(zip(
    ("state", "field_card", "rules", "payments", "draft", "counters", "pair_offers", "events", "own_turn"),
    ((0, 594), (594, 0), (594, 15), (609, 86), (695, 152), (847, 54), (901, 720), (1621, 1792), (3413, 321))))


def profile(name):
    from boardcraft.games.rentcraft.inference.profiles import resolve_profile
    return resolve_profile(name, 1)


def fresh(n=3):
    return Game.new(GameConfig(n, 129, trade_windows="off"))


def decision_game(game, overlay=None):
    from boardcraft.games.rentcraft.inference.tradebuilder import DecisionGame
    return DecisionGame(game.state, game.decision(), overlay)


@pytest.mark.parametrize("name,layout,size", [(MINI, MINI_LAYOUT, 1055), (V7, V7_LAYOUT, 3734)])
@pytest.mark.parametrize("n", [2, 3, 4])
def test_exact_layout_float32_and_empty_seats(name, layout, size, n):
    e = profile(name).encoder()
    assert e.layout == layout and e.size == size
    game = fresh(n)
    seat = game.decision().player
    dg = decision_game(game)
    before = copy.deepcopy(vars(game.state.memory))
    v = e.encode(dg.state, seat, dg)
    assert v.shape == (size,) and v.dtype == np.float32 and np.isfinite(v).all()
    assert vars(game.state.memory) == before
    stride, start = (47, 8) if name == MINI else (52, 0)
    assert not v[start + n * stride:start + 4 * stride].any()
    np.testing.assert_array_equal(e.encode(dg.state, seat, dg), v)


def test_closed_profile_resolver():
    from boardcraft.games.rentcraft.inference.profiles import resolve_profile
    for name, version in [(MINI, 2), (V7, 0), ("v7", 1), ("unknown", 1)]:
        with pytest.raises(ValueError):
            resolve_profile(name, version)


def test_v7_entry_codes_order_and_token_partition():
    from boardcraft.games.rentcraft.inference.tokens import token_layout, index_tables
    game = fresh(3)
    s, p = game.state, game.state.current
    r = (p + 1) % 3
    s.memory.log = [LogEntry("card", p, card=("opportunity", 8), round=1, turn=1),
                    LogEntry("trade_reject", r, p, give=(1,), get=(3,), amount=25, round=1, turn=1)]
    s.memory.pair_offers[p][r] = [PairOffer((1,), (3,), 25, (1, 0), "rejected", 1, False)]
    s.memory.model_history.own_acts = [ModelAct("offer", p, 1, r, give=(1,), get=(3,), amount=25,
                                               outcome="rejected", phase="pre_roll")]
    dg = decision_game(game)
    v = profile(V7).encoder().encode(dg.state, p, dg)
    np.testing.assert_array_equal(v[901:916], [2, 0, 0, 4, 0, 0, 2, 2, 1, 0,
                                             np.float32(math.log1p(25 / 500)), 1, 0, 0, 0])
    np.testing.assert_array_equal(v[1621:1632], [14, 1, 2, 0, 0, 2, 0, 0, 4, 0, 0])
    assert v[1621 + 28 + 4] == 9
    np.testing.assert_array_equal(v[3430:3444], [9, 2, 0, 2, 0, 0, 4, 0, 0, 2, 0, 1, 0, 0])
    layout = token_layout(V7)
    assert layout.player_idx.shape == (4, 82) and layout.space_idx.shape == (28, 16)
    assert [(b, len(ix)) for b, ix in layout.globals] == [
        ("state", 22), ("rules", 15), ("payments", 6), ("draft", 12), ("own_turn", 17)]
    assert layout.n_tokens == 181 and layout.field_card.shape == (28, 27)
    ix = [layout.player_idx.ravel(), layout.space_idx.ravel(),
          *(a for _, a in layout.globals), *(a.ravel() for a in layout.entry_idx.values())]
    used = np.concatenate(ix)
    used = used[used != 3734]
    np.testing.assert_array_equal(np.sort(used), np.arange(3734))
    tables = index_tables(layout.fields)
    assert [t for t, _, _ in tables] == [
        "kind", "who", "to", "space", "card", "give", "get", "up", "down", "outcome",
        "direction", "edit_kind", "turn_kind", "origin", "phase", "window", "window_actor"]
    assert sum(len(ix) for _, _, ix in tables) == 1776
    assert 3734 - 1776 + 1776 * 8 == 16166


def test_v7_compact_management_uses_first_occurrence_and_signed_cash():
    game = fresh()
    p = game.state.current
    game.state.memory.log = [LogEntry("build", p, space=1, amount=50, turn=1),
                             LogEntry("mortgage", p, space=3, amount=30, turn=1),
                             LogEntry("build", p, space=1, amount=50, turn=1)]
    dg = decision_game(game)
    row = profile(V7).encoder().encode(dg.state, p, dg)[1621:1649]
    np.testing.assert_array_equal(row[:18], [20, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 0, 0, 4, 0, 0, 0])
    np.testing.assert_array_equal(row[22:26], np.array([math.log1p(2/5), 0, math.log1p(1/5), 0], np.float32))
    assert row[26] == np.float32(-math.log1p(70/100))


def test_mini_changes_use_engine_snapshot_and_relative_seats():
    game = fresh()
    s, p = game.state, game.state.current
    old = model_snapshot(s)
    s.memory.model_history.snaps[p] = old
    s.players[p].cash += 250
    s.owner[1] = p
    s.memory.paid[(p+1)%3][p] += 50
    dg = decision_game(game)
    row = profile(MINI).encoder().encode(dg.state, p, dg)[1027:]
    np.testing.assert_array_equal(row[:7], np.array([.5, .31, 1, 0, 0, 0, 0], np.float32))
    assert row[7+5] == np.float32(.1)
    assert old.cash[p] == 1500


def test_mini_cards_ignore_hidden_prefix_and_use_public_bottom():
    game = fresh()
    s, p = game.state, game.state.current
    enc = profile(MINI).encoder()
    def cards():
        dg = decision_game(game)
        return enc.encode(dg.state, p, dg)[1007:1027]
    initial = cards()
    for deck in s.decks:
        s.decks[deck].reverse()
    np.testing.assert_array_equal(cards(), initial)
    # Exact float32 repeated-add fractions, rather than one final division.
    count = np.float32(0)
    for _ in range(8):
        count += 1/16
    assert initial[2+2] == count
    for deck in s.decks:
        s.memory.seen_cards[deck] = set(range(16))
        s.memory.known_bottom[deck] = list(range(16))
    s.memory.known_bottom["opportunity"] = [7, *range(7), *range(8, 16)]
    known = cards()
    assert known[0] == 1 and known[1] == 0 and known[2+3] == 1 and known[9] == .25
    # Holding or transferring the release card removes it from the circulating set.
    s.players[p].jail_cards = ["opportunity"]
    s.memory.known_bottom["opportunity"].remove(8)
    s.memory.seen_cards["opportunity"] = set(range(16)) - {8}
    held = cards()
    s.players[p].jail_cards.clear()
    s.players[(p+1)%3].jail_cards = ["opportunity"]
    np.testing.assert_array_equal(cards(), held)
    s.players[(p+1)%3].jail_cards.clear()
    s.memory.known_bottom["opportunity"].append(8)
    s.memory.seen_cards["opportunity"].add(8)
    np.testing.assert_array_equal(cards(), known)


def test_v7_never_calls_candidate_generator(monkeypatch):
    from boardcraft.games.rentcraft.inference import actions
    def forbidden(*args, **kwargs):
        raise AssertionError("v7 called the generator")
    for name in ("proposals", "counter_for", "debtor_sales", "payer_purchases", "raise_sales"):
        monkeypatch.setattr(actions, name, forbidden)
    game = fresh()
    dg = decision_game(game)
    p = profile(V7)
    p.action_space().mask(dg)
    p.encoder().encode(dg.state, game.state.current, dg)


def test_mini_ordered_candidates_and_all_nine_features(monkeypatch):
    from boardcraft.games.rentcraft.inference import actions
    from boardcraft.games.rentcraft.trade import TradeOffer
    from boardcraft.games.rentcraft.bots.trading import trade_score
    game = fresh()
    s, p = game.state, game.state.current
    r = (p+1)%3
    offers = [TradeOffer(p, r, give_cash=c) for c in (50, 25, 100, 75, 125)]
    monkeypatch.setattr(actions, "proposals", lambda *a, **kw: offers)
    dg = decision_game(game)
    space = profile(MINI).action_space()
    assert space.proposal_candidates(dg, p) == offers[:4]
    v = profile(MINI).encoder().encode(dg.state, p, dg)
    expected = [[1, 1/3, 0, 0, o.give_cash/1500, 0, 0, 0,
                 max(-5, min(trade_score(s, o, p)/1000, 5))] for o in offers[:4]] + [[0]*9]
    np.testing.assert_array_equal(v[516:561].reshape(5,9), np.array(expected, np.float32))


def test_observation_projection_refuses_missing_history_and_has_no_hidden_decks():
    from boardcraft.games.rentcraft.inference.tradebuilder import DecisionGame
    from .test_model_history import fresh as public_fresh
    game = public_fresh()
    p = game.decision().player
    obs = game.observe(p)
    dg = DecisionGame.from_observation(obs)
    assert not dg.state.decks
    for name in (MINI, V7):
        v = profile(name).encoder().encode(dg.state, p, dg)
        assert np.isfinite(v).all()
    obs.data["model_context"]["complete_from_start"] = False
    with pytest.raises(ValueError, match="complete"):
        DecisionGame.from_observation(obs)


def test_inference_source_is_english_isolated_and_has_no_game_state_globals():
    import ast
    from pathlib import Path
    from boardcraft.games.rentcraft import inference
    for path in Path(inference.__file__).parent.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert text.isascii(), path.name
        tree = ast.parse(text)
        assert not any(isinstance(n, (ast.Global, ast.Nonlocal)) for n in ast.walk(tree))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [n.name for n in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
                if node.level==0:
                    assert not (node.module or "").startswith("rentcraft")
            else:
                continue
            assert not any(name.startswith(("torch", "boardcraft_training")) for name in names)
        # No game-derived process caches or shared mutable decision workspace.
        assert "_CACHE" not in text and "_shared" not in text


def test_projection_and_private_notes_do_not_change_observation_or_other_seat():
    from boardcraft.games.rentcraft.inference.tradebuilder import DecisionGame,NoteOverlay
    from .test_model_history import fresh as public_fresh
    game = public_fresh()
    p = game.decision().player
    obs = game.observe(p)
    before = copy.deepcopy(obs.data)
    dg = DecisionGame.from_observation(obs,NoteOverlay(p))
    space = profile(V7).action_space()
    for i in [178,210,220]:
        space.to_action(dg,i)
    assert obs.data==before and game.observe(p).data==before
    other = game.observe((p+1)%3).data["model_context"]
    assert other["own_acts"]==[]
    assert not hasattr(game.game.state.memory,"model_acts")
