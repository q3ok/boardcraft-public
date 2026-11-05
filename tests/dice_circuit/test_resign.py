"""Surrender: a seated player leaves, the table ends and the final sheets are shown."""
import pytest
from boardcraft.contracts import Action, IllegalAction, StaleDecision
from boardcraft.registry import get_game
from engine_helpers import act, new

RESIGN = Action('resign', 'resign', {})


def resign(engine, seat):
    return engine.apply_seat_operation(seat, RESIGN, engine.observe(seat).revision)


def test_every_seat_can_resign_but_not_an_observer_or_a_finished_game():
    engine = new(3, seed=4)
    for seat in range(3):
        spec, = engine.command_specs(seat)
        assert spec['kind'] == 'resign' and spec['scope'] == 'seat'
        assert spec['params_schema'] == {'type': 'object', 'properties': {}, 'additionalProperties': False}
        assert spec['label']
    assert engine.command_specs(None) == ()
    resign(engine, 1)
    assert all(engine.command_specs(seat) == () for seat in (None, 0, 1, 2))


def test_resign_ends_the_game_with_the_resigning_seat_last():
    engine = new(3, seed=4)
    for _ in range(12):
        act(engine)
    before = [item['total'] for item in engine.observe(None).data['score_summary']]
    events = resign(engine, 0)
    assert [e.kind for e in events] == ['resigned', 'game_finished']
    assert events[0].payload == {'player': 0}
    assert engine.is_over() and engine.decision() is None
    assert all(engine.legal_actions(seat) == () for seat in range(3))
    result = engine.result()
    assert result.reason == 'resigned'
    assert list(result.scores) == before
    assert result.placements[0] == 3
    others = sorted((1, 2), key=lambda seat: -result.scores[seat])
    assert sorted(result.placements[1:]) in ([1, 2], [1, 1])
    assert result.placements[others[0]] == 1
    observed = engine.observe(None).data
    assert observed['resigned'] == 0 and observed['result']['reason'] == 'resigned'


def test_solo_resign_finishes_immediately():
    engine = new(1, seed=2)
    act(engine)
    resign(engine, 0)
    assert engine.is_over()
    assert engine.result().placements == (1,) and engine.result().reason == 'resigned'


def test_resign_during_a_simultaneous_window_reveals_the_final_sheets():
    engine = new(2, seed=6)
    for _ in range(200):
        if engine._state.phase == 'passive':
            break
        act(engine)
    assert engine._state.phase == 'passive'
    waiting = next(seat for seat in range(2) if engine.legal_actions(seat))
    resign(engine, waiting)
    data = engine.observe(None).data
    assert engine.is_over() and data['turn_phase'] == 'over'
    assert [item['total'] for item in data['score_summary']] == list(engine.result().scores)


def test_stale_or_unknown_operations_change_nothing():
    engine = new(2, seed=4)
    before = engine.snapshot()
    with pytest.raises(StaleDecision):
        engine.apply_seat_operation(0, RESIGN, engine.observe(0).revision + 1)
    with pytest.raises(IllegalAction):
        engine.apply_seat_operation(0, Action('request_trade', 'request_trade', {}), engine.observe(0).revision)
    with pytest.raises(IllegalAction):
        engine.apply_seat_operation(0, Action('resign', 'resign', {'x': 1}), engine.observe(0).revision)
    with pytest.raises(IllegalAction):
        engine.apply_seat_operation(5, RESIGN, 0)
    assert engine.snapshot() == before
    resign(engine, 1)
    with pytest.raises(IllegalAction):
        resign(engine, 0)


def test_resigned_game_round_trips_and_untouched_snapshots_keep_their_shape():
    module = get_game('dice_circuit')
    engine = new(2, seed=4)
    assert 'resigned' not in engine.snapshot()['state']
    act(engine)
    resign(engine, 1)
    saved = engine.snapshot()
    assert saved['state']['resigned'] == 1
    restored = module.restore(saved)
    assert restored.snapshot() == saved
    assert restored.result() == engine.result() and restored.is_over()
    damaged = dict(saved, state=dict(saved['state'], resigned=7))
    with pytest.raises(ValueError):
        module.restore(damaged)
    live = dict(saved, state=dict(saved['state'], phase='regular'))
    with pytest.raises(ValueError):
        module.restore(live)


def test_resign_projection_reaches_every_viewer():
    engine = new(2, seed=4)
    events = resign(engine, 0)
    for viewer in (None, 0, 1):
        assert [e.kind for e in engine.project_events(events, viewer)] == ['resigned', 'game_finished']
