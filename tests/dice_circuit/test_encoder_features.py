"""Literal feature contracts and hand-calculated visible-state examples."""
from copy import deepcopy
from dataclasses import replace

import numpy as np
import pytest

from boardcraft.contracts import Decision
from boardcraft.games.dice_circuit.encoder import DiceCircuitEncoder
from engine_helpers import new


def fields(spec):
    return {f['name']: f for f in spec['fields']}


def values(flat, descriptor):
    return flat[descriptor['offset']:descriptor['offset'] + descriptor['width']]


def test_exact_layout_offsets_shapes_and_semantic_tokens():
    enc = DiceCircuitEncoder()
    spec = enc.encoder_spec()
    assert (spec['encoder_id'], spec['version'], spec['observation_version'], spec['dtype'], spec['flat_size']) == (
        'dice-circuit-visible', '1', '1', 'float32', 3697)
    expected_global = [
        ('present', 0, 1), ('viewer_present', 1, 1), ('players', 2, 4), ('round', 6, 6),
        ('rounds_total', 12, 3), ('rounds_remaining', 15, 1), ('active_turns_left', 16, 1),
        ('passive_turns_left', 17, 1), ('turn_index', 18, 4), ('start_player', 22, 4),
        ('regular_opportunity', 26, 3), ('phase', 29, 6), ('turn_phase', 35, 5),
        ('active', 40, 4), ('decision_present', 44, 1), ('decision_owner', 45, 4),
        ('decision_kind', 49, 8), ('window_present', 57, 1), ('stage', 58, 6)]
    expected_seat = [
        ('present', 0, 1), ('active_done_this_round', 1, 1), ('seat_offset_from_active', 2, 4),
        ('yellow', 6, 16), ('blue', 22, 11), ('green', 33, 11),
        ('orange_present', 44, 11), ('orange', 55, 77), ('purple_present', 132, 11),
        ('purple', 143, 77), ('earned', 220, 35), ('rerolls_earned', 255, 7),
        ('rerolls_used', 262, 7), ('extras_earned', 269, 7), ('extras_used', 276, 7),
        ('emblem_count', 283, 1), ('area_scores', 284, 5), ('emblem_points', 289, 1),
        ('total_score', 290, 1)]
    expected = [('global.' + k, o, w) for k, o, w in expected_global]
    for i, die in enumerate(('yellow', 'blue', 'green', 'orange', 'purple', 'white')):
        expected.extend((f'die.{die}.' + k, 64 + i * 11 + o, w)
                        for k, o, w in [('present', 0, 1), ('value', 1, 6), ('zone', 7, 3), ('extra_used', 10, 1)])
    for seat in range(4):
        expected.extend((f'seat.{seat}.' + k, 130 + seat * 291 + o, w) for k, o, w in expected_seat)
    for frame in range(16):
        expected.extend((f'frame.{frame}.' + k, 1294 + frame * 143 + o, w) for k, o, w in [
            ('present', 0, 1), ('locked', 1, 1), ('top', 2, 1), ('pending_present', 3, 35),
            ('pending_kind', 38, 35), ('ancestry_present', 73, 35), ('ancestry_rank', 108, 35)])
    expected.extend(('current.' + k, 3582 + o, w) for k, o, w in [
        ('present', 0, 1), ('source', 1, 35), ('kind', 36, 9), ('ancestry_present', 45, 35),
        ('ancestry_rank', 80, 35)])
    assert [(f['name'], f['offset'], f['width']) for f in spec['fields']] == expected
    assert [i for f in spec['fields'] for i in range(f['offset'], f['offset'] + f['width'])] == list(range(3697))
    descriptors = fields(spec)
    for area in ('orange', 'purple'):
        d = descriptors['seat.0.' + area]
        assert d['shape'] == [11, 7] and d['vocabulary'] == list(range(7))
        assert d['encoding'] == 'one_hot' and d['normalization']['divisor'] == 1
    for name in ('global.players', 'global.round', 'global.rounds_total', 'global.regular_opportunity', 'die.white.value'):
        assert descriptors[name]['encoding'] == 'one_hot'
    for name in ('rerolls_earned', 'rerolls_used', 'extras_earned', 'extras_used'):
        assert descriptors['seat.0.' + name]['encoding'] == 'thermometer'
    groups = {g['id']: g for g in spec['groups']}
    assert {k: (len(v['entity_ids']), len(v['feature_indices'][0])) for k, v in groups.items()} == {
        'global': (1, 64), 'dice': (6, 11), 'seats': (4, 291), 'frames': (16, 143), 'current': (1, 115)}
    assert [i for g in groups.values() for token in g['feature_indices'] for i in token] == list(range(3697))
    assert groups['seats']['presence_indices'] == [130, 421, 712, 1003]
    assert groups['frames']['presence_indices'] == list(range(1294, 3582, 143))


@pytest.mark.parametrize('players,total', [(1, 6), (2, 6), (3, 5), (4, 4)])
def test_initial_horizons_one_hots_observer_and_absent_tokens(players, total):
    enc, engine = DiceCircuitEncoder(), new(players, 13)
    spec = enc.encoder_spec()
    descriptors = fields(spec)
    for viewer in (*range(players), None):
        obs = engine.observe(viewer)
        flat = enc.encode(obs)
        assert flat.dtype == np.float32 and flat.shape == (3697,) and np.isfinite(flat).all()
        np.testing.assert_array_equal(values(flat, descriptors['global.players']), np.eye(4)[players - 1])
        np.testing.assert_array_equal(values(flat, descriptors['global.round']), [1, 0, 0, 0, 0, 0])
        np.testing.assert_array_equal(values(flat, descriptors['global.rounds_total']), np.eye(3)[total - 4])
        assert values(flat, descriptors['global.rounds_remaining']).item() == pytest.approx((total - 1) / 6)
        assert values(flat, descriptors['global.active_turns_left']).item() == pytest.approx(0 if viewer is None else total / 6)
        assert values(flat, descriptors['global.passive_turns_left']).item() == pytest.approx(
            0 if viewer is None else total * max(players - 1, 1) / 12)
        for seat in range(players, 4):
            assert not flat[130 + seat * 291:130 + (seat + 1) * 291].any()
        # Empty fields are real zero-valued categories on present seats.
        for seat in range(players):
            np.testing.assert_array_equal(values(flat, descriptors[f'seat.{seat}.orange']).reshape(11, 7),
                                          np.tile([1, 0, 0, 0, 0, 0, 0], (11, 1)))
        if viewer is None:
            assert not flat[44:64].any()


def test_hand_calculated_values_resources_faces_rotated_seats_and_scores():
    enc = DiceCircuitEncoder()
    obs = new(3, 7).observe(2)
    data = deepcopy(dict(obs.data))
    data.update(round=3, rounds_total=5, rounds_remaining=2, active_turns_left=3, passive_turns_left=5,
                turn_index=1, start_player=0, active_player=1, regular_opportunity=2, phase='bonus', turn_phase='regular')
    for seat, item in enumerate(data['sheets']):
        item['active_done_this_round'] = seat == 0
        item['seat_offset_from_active'] = (seat - 1) % 3
    data['sheets'][2].update(orange=[1, 2, 3, 8, 5, 6, 12, 1, 4, 3, 18], purple=[1, 2, 6, 1],
        blue=[2, 3], green=2, emblem_count=2, rerolls=2, rerolls_earned=5, rerolls_used=3,
        extras=1, extras_earned=2, extras_used=1)
    for die, face in zip(('yellow', 'blue', 'green', 'orange', 'purple', 'white'), range(1, 7)):
        data['dice'][die] = {'value': face, 'zone': 'kept' if die == 'white' else 'available'}
    data['window']['extra_used'] = ['white']
    modified = replace(obs, data=data, decision=Decision('opaque', 2, 'place_bonus'))
    spec, flat = enc.encoder_spec(), enc.encode(modified)
    descriptors = fields(spec)
    def feature(name):
        return values(flat, descriptors[name])
    np.testing.assert_array_equal(feature('global.active'), [0, 0, 1, 0])
    np.testing.assert_array_equal(feature('global.start_player'), [0, 1, 0, 0])
    np.testing.assert_array_equal(feature('global.turn_index'), [0, 1, 0, 0])
    np.testing.assert_array_equal(feature('global.decision_owner'), [1, 0, 0, 0])
    np.testing.assert_array_equal(feature('global.decision_kind'), [0, 0, 1, 0, 0, 0, 0, 0])
    np.testing.assert_array_equal(feature('global.phase'), [0, 0, 0, 0, 0, 1])
    np.testing.assert_array_equal(feature('global.turn_phase'), [1, 0, 0, 0, 0])
    np.testing.assert_array_equal(feature('global.regular_opportunity'), [0, 1, 0])
    np.testing.assert_array_equal(feature('seat.0.seat_offset_from_active'), [0, 1, 0, 0])
    np.testing.assert_array_equal(feature('seat.1.seat_offset_from_active'), [0, 0, 1, 0])
    np.testing.assert_array_equal(feature('seat.2.seat_offset_from_active'), [1, 0, 0, 0])
    assert feature('seat.0.active_done_this_round') == 0
    assert feature('seat.1.active_done_this_round') == 1
    np.testing.assert_array_equal(feature('seat.0.rerolls_earned'), [1, 1, 1, 1, 1, 0, 0])
    np.testing.assert_array_equal(feature('seat.0.rerolls_used'), [1, 1, 1, 0, 0, 0, 0])
    np.testing.assert_array_equal(feature('seat.0.extras_earned'), [1, 1, 0, 0, 0, 0, 0])
    np.testing.assert_array_equal(feature('seat.0.extras_used'), [1, 0, 0, 0, 0, 0, 0])
    np.testing.assert_array_equal(feature('seat.0.orange').reshape(11, 7), np.eye(7)[[1, 2, 3, 4, 5, 6, 6, 1, 2, 3, 6]])
    np.testing.assert_array_equal(feature('seat.0.purple').reshape(11, 7), np.eye(7)[[1, 2, 6, 1, 0, 0, 0, 0, 0, 0, 0]])
    np.testing.assert_allclose(feature('seat.0.area_scores'), np.array([0, 2, 3, 63, 10]) / [60, 56, 66, 96, 66])
    assert feature('seat.0.emblem_count').item() == pytest.approx(2 / 5)
    assert feature('seat.0.emblem_points').item() == 0
    assert feature('seat.0.total_score').item() == pytest.approx(78 / 624)
    for die, face in zip(('yellow', 'blue', 'green', 'orange', 'purple', 'white'), range(1, 7)):
        np.testing.assert_array_equal(feature(f'die.{die}.value'), np.eye(6)[face - 1])
    np.testing.assert_array_equal(feature('die.white.zone'), [0, 1, 0])
    assert feature('die.white.extra_used') == 1
    # Transport metadata and precomputed summaries are not neural inputs.
    altered = deepcopy(data)
    altered['event_cursor'] = 99999
    altered['score_summary'] = []
    np.testing.assert_array_equal(enc.encode(replace(modified, data=altered, revision=99999)), flat)


def test_rejects_unsupported_versions_and_keeps_legal_mask_out_of_features():
    enc, engine = DiceCircuitEncoder(), new(1)
    obs = engine.observe(0)
    with pytest.raises(ValueError, match='observation version'):
        enc.encode(replace(obs, observation_version='999'))
    flat = enc.encode(obs)
    mask = enc.mask(obs, engine.legal_actions(0))
    assert mask.dtype == np.bool_ and mask.shape == (301,) and mask.any()
    np.testing.assert_array_equal(enc.encode(obs), flat)
