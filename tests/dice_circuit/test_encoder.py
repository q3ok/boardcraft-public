"""Versioned codecs preserve visible semantics and every legal action."""
from copy import deepcopy
import importlib.util
import hashlib
import json
import numpy as np
import pytest
from boardcraft.contracts import Action, Observation
from engine_helpers import new


def encoder():
    assert importlib.util.find_spec('boardcraft.games.dice_circuit.encoder') is not None, 'Observation encoder is missing'
    from boardcraft.games.dice_circuit.encoder import DiceCircuitEncoder
    return DiceCircuitEncoder()


def field(spec, name):
    item = next(f for f in spec['fields'] if f['name'] == name)
    return slice(item['offset'], item['offset'] + item['width'])


def changed(obs, **changes):
    data = deepcopy(dict(obs.data))
    data.update(changes)
    return Observation(obs.game_id, obs.rules_version, obs.observation_version, obs.viewer, obs.revision + 999, obs.decision, data)


def test_catalogue_v1_is_bijective_and_rejects_semantic_id_spoofing():
    enc = encoder()
    spec = enc.action_spec()
    assert (spec['version'], spec['size']) == ('1', 301)
    assert hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(',', ':')).encode()).hexdigest() == 'c4d8fd08a5ed20446d01687a6c4bd384d4d494c9d1123f1431eb0fe7061c1b39'
    actions = [enc.decode_action(i) for i in range(301)]
    assert len({a.id for a in actions}) == 301
    assert {k: sum(a.kind == k for a in actions) for k in {a.kind for a in actions}} == {
        'select_die': 112, 'extra_die': 112, 'place_bonus': 56, 'choose_bonus': 16,
        'choose_round_bonus': 2, 'reroll': 1, 'finish_extra': 1, 'decline_reroll': 1}
    assert actions[0].params == {'area': 'yellow', 'target': [0, 0], 'die_id': 'yellow'}
    assert actions[111].params == {'area': 'purple', 'target': 10, 'die_id': 'white'}
    assert actions[280].params == {'bonus_id': 'yellow:row:0'}
    assert [a.kind for a in actions[-3:]] == ['reroll', 'finish_extra', 'decline_reroll']
    for i, action in enumerate(actions):
        assert enc.encode_action(action) == i
    with pytest.raises(ValueError):
        enc.encode_action(Action(actions[0].id, 'reroll', {}))
    with pytest.raises(ValueError):
        enc.encode_action(Action(actions[0].id, actions[0].kind, {'area': 'yellow', 'target': [False, 0.0], 'die_id': 'yellow'}))
    for bad in (-1, 301, True, 0.0):
        with pytest.raises(ValueError):
            enc.decode_action(bad)


@pytest.mark.parametrize('players', [1, 2, 3, 4])
def test_relative_seats_presence_real_zeros_and_descriptor_maps(players):
    enc = encoder()
    engine = new(players)
    viewer = players - 1
    obs = engine.observe(viewer)
    flat, spec = enc.encode(obs), enc.encoder_spec()
    assert (spec['version'], spec['flat_size'], flat.dtype) == ('1', 3697, np.float32)
    assert flat.shape == (3697,) and np.isfinite(flat).all()
    indices = [i for f in spec['fields'] for i in range(f['offset'], f['offset'] + f['width'])]
    assert indices == list(range(3697))
    assert all('raw_bounds' in f and 'encoding' in f for f in spec['fields'])
    assert next(f for f in spec['fields'] if f['name'] == 'seat.0.yellow')['shape'] == [4, 4]
    groups = {g['id']: g for g in spec['groups']}
    for group in groups.values():
        for entity, indices, presence in zip(group['entity_ids'], group['feature_indices'], group['presence_indices']):
            descriptors = [f for f in spec['fields'] if f['group'] == group['id'] and f['entity'] == entity]
            assert indices == [i for f in descriptors for i in range(f['offset'], f['offset'] + f['width'])]
            assert presence in indices
            assert group['padding_value'] == 0
    for seat in range(4):
        assert flat[field(spec, f'seat.{seat}.present')].item() == (seat < players)
        assert flat[field(spec, f'seat.{seat}.rerolls_earned')].sum() == 0 if seat >= players else True
        if seat >= players:
            assert not flat[groups['seats']['feature_indices'][seat]].any()
    assert flat[field(spec, 'seat.0.present')].item() == 1
    assert flat[field(spec, 'seat.0.blue')].sum() == 0
    assert flat[field(spec, 'seat.0.yellow')].sum() == 4
    data = deepcopy(dict(obs.data))
    data['sheets'][viewer]['blue'] = [2]
    shifted = enc.encode(changed(obs, **data))
    assert shifted[field(spec, 'seat.0.blue')][0] == 1
    assert np.array_equal(flat, enc.encode(changed(obs, event_cursor=999999)))
    spec['fields'].clear()
    assert enc.encoder_spec()['fields']


def test_descriptor_relative_seats_and_scores_do_not_clip_or_use_opaque_metadata():
    enc, engine = encoder(), new(3)
    obs = engine.observe(2)
    data = deepcopy(dict(obs.data))
    data['sheets'][2]['emblem_count'] = 99
    data['sheets'][0]['blue'] = [2]
    data['sheets'][1]['blue'] = [3]
    modified = changed(obs, **data)
    flat, spec = enc.encode(modified), enc.encoder_spec()
    assert flat[field(spec, 'seat.0.emblem_count')].item() > 1
    assert flat[field(spec, 'seat.1.blue')][0] == 1
    assert flat[field(spec, 'seat.2.blue')][1] == 1
    from boardcraft.contracts import Decision
    opaque = Observation(obs.game_id, obs.rules_version, obs.observation_version, obs.viewer, 555,
                         None if obs.decision is None else Decision('opaque-changed', obs.decision.player, obs.decision.kind), data)
    assert np.array_equal(flat, enc.encode(opaque))


def test_owned_stack_encodes_locked_frames_ordered_ancestry_and_selected_zero_padding():
    enc = encoder()
    obs = new(1).observe(0)
    stack = {'frames': [
        {'pending': [{'source_id': 'yellow:row:0', 'kind': 'blueX'}], 'ancestry': []},
        {'pending': [], 'ancestry': ['blue:row:1']}],
        'current': {'id': 'green:position:6', 'kind': 'blueX', 'ancestry': ['blue:row:1']}}
    data = deepcopy(dict(obs.data))
    data['window']['bonus_stack'] = stack
    flat, spec = enc.encode(changed(obs, **data)), enc.encoder_spec()
    assert flat[field(spec, 'frame.0.present')].item() == 1
    assert flat[field(spec, 'frame.0.locked')].item() == 1
    assert flat[field(spec, 'frame.1.top')].item() == 1
    assert flat[field(spec, 'frame.1.locked')].item() == 1
    assert flat[field(spec, 'frame.1.pending_present')].sum() == 0
    assert flat[field(spec, 'frame.1.ancestry_present')].sum() == 1
    assert flat[field(spec, 'current.present')].item() == 1
    assert flat[field(spec, 'current.source')].sum() == 1
    assert flat[field(spec, 'frame.2.present')].item() == 0
    observer = new(1).observe(None)
    assert enc.encode(observer)[field(spec, 'current.present')].item() == 0


def test_encoder_retains_full_finite_source_depth_and_ancestor_order():
    enc, obs = encoder(), new(1).observe(0)
    from boardcraft.games.dice_circuit.actions import reward_catalog
    sources = list(dict.fromkeys(r.source_id for r in reward_catalog() if r.kind not in ('reroll', 'extra', 'endgame')))
    assert len(sources) == 16
    data = deepcopy(dict(obs.data))
    data['window']['bonus_stack'] = {
        'frames': [{'pending': [], 'ancestry': sources[:depth]} for depth in range(16)],
        'current': {'id': sources[-1], 'kind': 'black6', 'ancestry': sources[:-1]}}
    flat, spec = enc.encode(changed(obs, **data)), enc.encoder_spec()
    assert flat[field(spec, 'frame.15.present')].item() == 1
    assert flat[field(spec, 'current.ancestry_present')].sum() == 15
    assert max(flat[field(spec, 'current.ancestry_rank')]) == 15 / 16
    data['window']['bonus_stack']['current']['ancestry'].reverse()
    reversed_flat = enc.encode(changed(obs, **data))
    assert not np.array_equal(flat[field(spec, 'current.ancestry_rank')], reversed_flat[field(spec, 'current.ancestry_rank')])


def test_reachable_masks_equal_all_legal_actions_and_end_state_has_empty_mask():
    enc, engine = encoder(), new(4, 19)
    seen = set()
    for _ in range(2000):
        if engine.is_over():
            break
        d = engine.decision()
        obs, legal = engine.observe(d.player), engine.legal_actions(d.player)
        mask = enc.mask(obs, legal)
        assert mask.dtype == np.bool_ and mask.shape == (301,)
        assert {enc.decode_action(int(i)).id for i in np.flatnonzero(mask)} == {a.id for a in legal}
        assert np.isfinite(enc.encode(obs)).all()
        seen.update(a.kind for a in legal)
        selected = next((a for a in legal if a.kind == 'extra_die'), legal[0])
        engine.apply(d.player, selected, d.id)
    assert engine.is_over()
    assert {'place_bonus', 'choose_round_bonus', 'finish_extra', 'extra_die'} <= seen
    assert not enc.mask(engine.observe(0), ()).any()
