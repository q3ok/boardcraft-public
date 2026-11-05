"""Future-key forks preserve exact current state and strict legacy persistence."""

from copy import deepcopy

import pytest
from boardcraft.contracts import GameConfig
from boardcraft.games.dice_circuit.engine import DiceCircuitEngine


@pytest.mark.parametrize("players", [1, 2, 3, 4])
def test_future_only_fork_and_clone(players):
    source = DiceCircuitEngine.new(GameConfig("dice_circuit", "base", players), 7)
    for _ in range(6):
        before = source.snapshot()
        branch = source.fork(11)
        expected = {**before, "future_seed": 11, "forked": True}
        assert branch.snapshot() == expected
        assert source.snapshot() == before
        for seat in [*range(players), None]:
            assert branch.observe(seat) == source.observe(seat)
            assert branch.legal_actions(seat) == source.legal_actions(seat)
            assert branch.project_events(
                source.initial_events(), seat
            ) == source.project_events(source.initial_events(), seat)
        assert branch.clone().snapshot() == expected
        assert branch.restore(expected).snapshot() == expected
        assert branch.fork(13).snapshot() == {**expected, "future_seed": 13}
        assert source.fork(7).snapshot()["forked"] is True
        assert branch._random == branch.clone()._random or branch._random.die(
            1, 0, "active", 1, 0, "yellow"
        ) == branch.clone()._random.die(1, 0, "active", 1, 0, "yellow")
        decision = source.decision()
        source.apply(
            decision.player, source.legal_actions(decision.player)[0], decision.id
        )


def test_legacy_and_strict_versions():
    engine = DiceCircuitEngine.new(GameConfig("dice_circuit", "base", 1), 7)
    current = engine.snapshot()
    assert (
        current["format"] == 2
        and current["future_seed"] == 7
        and current["forked"] is False
    )
    legacy = {
        k: deepcopy(v) for k, v in current.items() if k not in ("future_seed", "forked")
    }
    legacy["format"] = 1
    assert DiceCircuitEngine.restore(legacy).snapshot() == current
    for mutation in (
        {"format": True},
        {"format": "2"},
        {"format": 3},
        {"forked": 1},
        {"future_seed": 8},
        {"future_seed": True},
        {"future_seed": -1},
        {"future_seed": 2**64},
    ):
        with pytest.raises(ValueError):
            DiceCircuitEngine.restore({**current, **mutation})
    for key in ("future_seed", "forked"):
        bad = deepcopy(current)
        del bad[key]
        with pytest.raises(ValueError):
            DiceCircuitEngine.restore(bad)
    with pytest.raises(ValueError):
        DiceCircuitEngine.restore({**legacy, "future_seed": 7})


@pytest.mark.parametrize("seed", [True, -1, 2**64, 1.0, None])
def test_bad_seed_validated_before_clone(seed, monkeypatch):
    engine = DiceCircuitEngine.new(GameConfig("dice_circuit", "base", 1), 7)
    monkeypatch.setattr(
        engine, "clone", lambda: pytest.fail("Cloned before seed validation")
    )
    with pytest.raises(ValueError):
        engine.fork(seed)


def test_same_key_same_future_and_distinct_keyed_streams():
    source = DiceCircuitEngine.new(GameConfig("dice_circuit", "base", 1), 7)
    left, right = source.fork(11), source.fork(11)
    for _ in range(12):
        decision = left.decision()
        action = left.legal_actions(decision.player)[0]
        assert left.apply(decision.player, action, decision.id) == right.apply(
            decision.player, action, decision.id
        )
        assert left.snapshot() == right.snapshot()
    other = source.fork(13)
    coordinates = [(1, 0, "active", 1, i, "yellow") for i in range(12)]
    assert [left._random.die(*c) for c in coordinates] != [
        other._random.die(*c) for c in coordinates
    ]


def test_fork_never_draws_current_chance_and_has_no_mutable_alias(monkeypatch):
    from boardcraft.games.dice_circuit.randomness import KeyedRandom

    engine = DiceCircuitEngine.new(GameConfig("dice_circuit", "base", 2), 7)
    before = engine.snapshot()

    def forbidden(*args):
        pytest.fail("Fork consumed chance or recomputed starting seat")

    for name in ("die", "proximity", "starting_seat"):
        monkeypatch.setattr(KeyedRandom, name, forbidden)
    fork = engine.fork(11)
    fork._state.sheets[0].rerolls += 1
    fork._revisions[0] += 1
    fork._state.windows[0].extra_used.add("yellow")
    assert engine.snapshot() == before
