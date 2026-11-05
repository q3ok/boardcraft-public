"""Explicit engine copies: independent state, atomic transitions, no generic deep copies."""

from pathlib import Path
import random

import numpy as np
import pytest
from boardcraft.contracts import GameConfig
from boardcraft.games import dice_circuit
from boardcraft.games.dice_circuit.encoder import DiceCircuitEncoder
from boardcraft.games.dice_circuit.engine import DiceCircuitEngine
from boardcraft.games.dice_circuit.turn_flow import TurnFlow


def play(engine, moves, seed):
    choice = random.Random(seed)
    for _ in range(moves):
        decision = engine.decision()
        if engine.is_over() or decision is None:
            break
        actions = engine.legal_actions(decision.player)
        engine.apply(decision.player, choice.choice(actions), decision.id)
    return engine


@pytest.mark.parametrize("players", [1, 2, 3, 4])
@pytest.mark.parametrize("moves", [0, 7, 19, 33])
def test_clone_equals_a_validated_restore_and_continues_identically(players, moves):
    source = play(DiceCircuitEngine.new(GameConfig("dice_circuit", "base", players), 21), moves, 5)
    clone, restored = source.clone(), DiceCircuitEngine.restore(source.snapshot())
    assert clone.snapshot() == restored.snapshot() == source.snapshot()
    assert play(clone, 400, 9).snapshot() == play(restored, 400, 9).snapshot()


def test_clone_shares_no_mutable_game_state():
    source = play(DiceCircuitEngine.new(GameConfig("dice_circuit", "base", 2), 3), 12, 1)
    before = source.snapshot()
    clone = source.clone()
    state = clone._state
    sheet = state.sheets[0]
    sheet.yellow.add((0, 0))
    sheet.blue.add(2)
    sheet.orange.append(1)
    sheet.purple.append(1)
    sheet.earned_rewards.add("x")
    sheet.rerolls += 1
    state.windows[0].extra_used.add("yellow")
    state.windows[0].stack._frames.append(None)
    state.dice["yellow"] = 7 - state.dice["yellow"]
    state.available.append("x")
    state.kept.append("x")
    state.tray.append("x")
    clone._revisions[0] += 1
    clone._cursors[0] += 1
    clone._decisions[0] += 1
    assert source.snapshot() == before


def test_bonus_stack_copies_do_not_share_frames():
    from boardcraft.games.dice_circuit.actions import reward_catalog
    from boardcraft.games.dice_circuit.bonuses import BonusStack

    entries = [reward for reward in reward_catalog() if reward.kind not in ("reroll", "extra", "endgame")]
    stack = BonusStack(entries[:2])
    saved = stack.snapshot()
    branch = stack.copy()
    choice = branch.available_choices()[0]
    branch.select(choice.id)
    assert stack.snapshot() == saved and branch.snapshot() != saved


def test_observation_does_not_expose_frozen_sheets():
    engine = DiceCircuitEngine.new(GameConfig("dice_circuit", "base", 2), 3)
    while engine._state.phase != "passive":
        play(engine, 1, 4)
    frozen = engine.snapshot()["state"]["frozen_sheets"]
    data = engine.observe(None).data
    data["sheets"][0]["orange"].append(99)
    data["sheets"][0]["green"] = 99
    assert engine.snapshot()["state"]["frozen_sheets"] == frozen


def test_failed_transition_leaves_the_engine_untouched(monkeypatch):
    engine = play(DiceCircuitEngine.new(GameConfig("dice_circuit", "base", 1), 7), 5, 3)
    before = engine.snapshot()
    decision = engine.decision()
    action = engine.legal_actions(decision.player)[0]

    def broken(self, player, selected):
        self._state.sheets[player].rerolls += 3
        self._state.available.append("x")
        raise RuntimeError("broken transition")

    monkeypatch.setattr(TurnFlow, "_perform", broken)
    with pytest.raises(RuntimeError):
        engine.apply(decision.player, action, decision.id)
    assert engine.snapshot() == before


def test_game_code_has_no_generic_deep_copy():
    package = Path(next(iter(dice_circuit.__path__)))
    offenders = [path.name for path in package.glob("*.py") if "deepcopy" in path.read_text(encoding="utf-8")]
    assert offenders == []


@pytest.mark.parametrize("players", [1, 2, 4])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_unrecorded_fork_plays_the_same_game(players, seed):
    encoder = DiceCircuitEncoder()
    source = play(DiceCircuitEngine.new(GameConfig("dice_circuit", "base", players), seed), 5, seed)
    recorded, quiet = source.fork(99), source.fork(99, recorded=False)
    choice = random.Random(seed)
    while not recorded.is_over():
        decision = recorded.decision()
        assert quiet.decision() == decision and not quiet.is_over()
        actions = recorded.legal_actions(decision.player)
        assert quiet.legal_actions(decision.player) == actions
        for seat in range(players):
            expected, actual = recorded.observe(seat), quiet.observe(seat)
            assert actual.decision == expected.decision
            assert np.array_equal(encoder.encode(actual), encoder.encode(expected))
            assert np.array_equal(encoder.mask(actual, quiet.legal_actions(seat)),
                                  encoder.mask(expected, recorded.legal_actions(seat)))
        action = choice.choice(actions)
        recorded.apply(decision.player, action, decision.id)
        assert quiet.apply(decision.player, action, decision.id) == ()
    assert quiet.is_over() and quiet.result() == recorded.result()
    assert quiet._state.sheets == recorded._state.sheets


def test_unrecorded_fork_leaves_its_source_alone_and_cannot_be_saved():
    source = play(DiceCircuitEngine.new(GameConfig("dice_circuit", "base", 1), 4), 6, 2)
    before = source.snapshot()
    quiet = source.fork(5, recorded=False)
    play(quiet, 400, 3)
    assert source.snapshot() == before
    with pytest.raises(ValueError):
        quiet.snapshot()
    assert source.fork(5).snapshot()["forked"] is True


def test_legal_actions_are_detached_from_the_shared_values():
    engine = DiceCircuitEngine.new(GameConfig("dice_circuit", "base", 1), 4)
    decision = engine.decision()
    first = engine.legal_actions(decision.player)
    expected = [dict(a.params) for a in first]
    for selected in first:
        selected.params["area"] = "changed"
    assert [dict(a.params) for a in engine.legal_actions(decision.player)] == expected
    other = DiceCircuitEngine.new(GameConfig("dice_circuit", "base", 1), 4)
    assert [dict(a.params) for a in other.legal_actions(decision.player)] == expected
