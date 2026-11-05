"""Player-visible bonus flow, automatic receipts and score terminology."""
from dataclasses import asdict

import numpy as np
import pytest

from boardcraft.games.dice_circuit.encoder import DiceCircuitEncoder
from boardcraft.registry import get_game
from engine_helpers import DICE, act, regular, restore


def yellow_bonus(phase='regular'):
    engine = regular([1, 1, 1, 4, 1, 1], available=('orange',), roll=3, players=3)
    owner = engine.decision().player
    def fixture(s):
        if phase in ('passive', 'round_bonus'):
            owner_seat = (s['active'] + 1) % 3
            s.update(phase=phase, available=[], tray=list(DICE), kept=[])
            if phase == 'round_bonus':
                s['round_no'] = 4
            for p, window in enumerate(s['windows']):
                window['stage'] = ('round' if phase == 'round_bonus' else 'passive') if p != s['active'] else 'done'
            s['sheets'][owner_seat]['orange'] = [1, 2, 3, 8]
            s['frozen_sheets'] = [dict(sheet) for sheet in s['sheets']]
        else:
            s['sheets'][s['active']]['orange'] = [1, 2, 3, 8]
    engine = restore(engine, fixture)
    return engine, (owner + 1) % 3 if phase in ('passive', 'round_bonus') else owner


@pytest.mark.parametrize('phase', ['regular', 'passive', 'round_bonus'])
def test_bonus_phase_retains_turn_context_without_disclosing_private_chains(phase):
    engine, owner = yellow_bonus(phase)
    viewer = (owner + 1) % 3
    before = asdict(engine.observe(viewer))
    if phase == 'round_bonus':
        events = act(engine, player=owner, kind='choose_round_bonus', bonus='black6')
    else:
        events = act(engine, player=owner, kind='select_die', die_id='orange', area='orange')
    observation = engine.observe(owner)
    assert observation.data['phase'] == 'bonus'
    assert observation.data['turn_phase'] == phase
    enc = DiceCircuitEncoder()
    spec, flat = enc.encoder_spec(), enc.encode(observation)
    for name, value in [('phase', 'bonus'), ('turn_phase', phase)]:
        field = next(f for f in spec['fields'] if f['name'] == f'global.{name}')
        assert flat[field['offset']:field['offset'] + field['width']].tolist() == [float(v == value) for v in field['vocabulary']]
    if phase != 'regular':
        assert asdict(engine.observe(viewer)) == before
        assert engine.project_events(events, viewer) == ()
        assert engine.observe(None).data['phase'] == phase
    else:
        assert engine.observe(None).data['phase'] == 'bonus'


def automatic(events):
    return [event.payload for event in events if event.kind == 'bonus_auto_resolved']


def test_single_pending_bonus_is_selected_but_multiple_targets_remain_explicit():
    engine, owner = yellow_bonus()
    events = act(engine, kind='select_die', die_id='orange', area='orange')
    choices = engine.legal_actions(owner)
    assert len(choices) > 1 and {a.kind for a in choices} == {'place_bonus'}
    assert [e['action']['kind'] for e in automatic(events)] == ['choose_bonus']
    assert automatic(events)[0]['bonus_id'] == 'orange:position:5'
    assert engine.clone().observe(owner) == engine.observe(owner)


def test_forced_selection_and_placement_need_no_randomness(monkeypatch):
    engine = regular([1] * 6, available=('blue',), roll=3)
    owner = engine.decision().player
    engine = restore(engine, lambda s: s['sheets'][owner].update(blue=[6, 10]))
    def unexpected_randomness(*args):
        pytest.fail('Forced bonus resolution consumed randomness')
    monkeypatch.setattr('boardcraft.games.dice_circuit.randomness.KeyedRandom.die', unexpected_randomness)
    monkeypatch.setattr('boardcraft.games.dice_circuit.randomness.KeyedRandom.proximity', unexpected_randomness)
    events = act(engine, kind='select_die', die_id='blue', area='blue', target=2)
    assert engine.observe(owner).data['sheets'][owner]['green'] == 1
    resolved = automatic(events)
    assert [e['action']['kind'] for e in resolved] == ['choose_bonus', 'place_bonus']
    assert [e['bonus_id'] for e in resolved] == ['blue:column:1'] * 2
    assert resolved[1]['action']['params'] == {'area': 'green', 'target': 0}
    assert any(e.kind == 'bonus_auto_resolved' for e in engine.project_events(events, None))


def test_forced_bonus_with_no_target_expires_and_resumes_regular_roll():
    engine = regular([1] * 6)
    owner = engine.decision().player
    engine = restore(engine, lambda s: s['sheets'][owner].update(blue=[6, 10], green=11))
    events = act(engine, kind='select_die', die_id='blue', area='blue', target=2)
    assert engine.snapshot()['state']['regular_roll'] == 2
    assert engine.observe(owner).data['window']['bonus_stack'] == {'frames': [], 'current': None}
    assert [e['action']['kind'] for e in automatic(events)] == ['choose_bonus']


def test_multiple_pending_bonuses_remain_explicit_then_resolve_children_before_siblings():
    engine = regular([1, 1, 1, 1, 1, 2], available=('blue',), roll=3)
    owner = engine.decision().player
    engine = restore(engine, lambda s: s['sheets'][owner].update(
        blue=[2, 4, 7, 11], orange=[1, 2, 3], purple=[1, 2, 3, 4, 5],
        earned_rewards=['orange:position:3', 'purple:position:3', 'purple:position:4', 'purple:position:5']))
    events = act(engine, kind='select_die', die_id='blue', area='blue', target=3)
    choices = engine.legal_actions(owner)
    assert {a.params['bonus_id'] for a in choices} == {'blue:row:0', 'blue:column:2'}
    assert engine.observe(owner).data['phase'] == 'bonus'
    enc = DiceCircuitEncoder()
    assert {enc.decode_action(int(i)).id for i in np.flatnonzero(enc.mask(engine.observe(owner), choices))} == {a.id for a in choices}
    assert automatic(events) == []
    events = act(engine, kind='choose_bonus', bonus_id='blue:column:2')
    assert [e['action']['kind'] for e in automatic(events)] == ['place_bonus', 'choose_bonus']
    stack = engine.observe(owner).data['window']['bonus_stack']
    assert stack['current']['id'] == 'purple:position:6'
    assert stack['current']['ancestry'] == ['blue:column:2']
    assert stack['frames'][0]['pending'] == [{'source_id': 'blue:row:0', 'kind': 'orange5'}]
    assert {a.kind for a in engine.legal_actions(owner)} == {'place_bonus'}


def test_private_automatic_events_are_owner_only():
    engine, owner = yellow_bonus('passive')
    before = {p: asdict(engine.observe(p)) for p in (*range(3), None) if p != owner}
    events = act(engine, player=owner, kind='select_die', die_id='orange', area='orange')
    assert automatic(engine.project_events(events, owner))
    for viewer, observation in before.items():
        assert engine.project_events(events, viewer) == ()
        assert asdict(engine.observe(viewer)) == observation


def test_emblem_count_and_points_are_distinct_in_sheet_scores_and_encoder():
    engine = regular([1] * 6)
    owner = engine.decision().player
    def fixture(s):
        s['sheets'][owner].update(yellow=[[r, c] for r in range(4) for c in range(4)],
                                blue=[2, 3], green=2, orange=[4], purple=[5])
        s['sheets'][owner]['emblem_count'] = 2
    engine = restore(engine, fixture)
    observation = engine.observe(owner)
    sheet, summary = observation.data['sheets'][owner], observation.data['score_summary'][owner]
    assert sheet['emblem_count'] == 2 and 'emblems' not in sheet
    assert summary == {'areas': {'yellow': 60, 'blue': 2, 'green': 3, 'orange': 4, 'purple': 5}, 'emblem_points': 4, 'total': 78}
    enc = DiceCircuitEncoder()
    flat, spec = enc.encode(observation), enc.encoder_spec()
    for name, value in [('emblem_count', 2), ('emblem_points', 4)]:
        field = next(f for f in spec['fields'] if f['name'] == f'seat.0.{name}')
        assert np.isclose(flat[field['offset']], value / field['normalization']['divisor'])


@pytest.mark.parametrize('area,params,human', [
    ('yellow', {'die_id': 'yellow', 'target': [2, 3]}, 'row 3, column 4'),
    ('orange', {'die_id': 'orange', 'target': 0}, 'field 1'),
])
def test_indexed_action_labels_explain_human_and_machine_coordinates(area, params, human):
    engine = regular([4] * 6)
    action = next(a for a in engine.legal_actions(engine.decision().player)
                  if a.params.get('area') == area and all(a.params.get(k) == v for k, v in params.items()))
    label = get_game('dice_circuit').describe_action(action)
    assert human in label
    assert '1-based' in label and 'target indices are 0-based' in label
