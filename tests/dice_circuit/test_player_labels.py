"""Contextual entry labels and named scores at the player-facing boundaries."""
from dataclasses import asdict

import numpy as np
import pytest

from boardcraft.contracts import GameConfig
from boardcraft.registry import get_game
from boardcraft.server.service import PlayService
from engine_helpers import act, regular, restore


@pytest.mark.parametrize('area,values,progress,target,phrases', [
    ('orange', [1, 1, 1, 3, 1, 1], [1, 2, 3], 3, ('write 6', 'x2')),
    ('orange', [1, 1, 1, 4, 1, 1], [1, 2], 2, ('write 4', 'x1', 'reroll')),
    ('purple', [1, 1, 1, 1, 6, 1], [1, 2, 3], 3, ('write 6', 'blueX')),
    ('green', [1, 1, 4, 1, 1, 1], 3, 3, ('cross', 'extra')),
])
def test_track_labels_explain_recorded_entry_multiplier_and_new_reward(area, values, progress, target, phrases):
    engine = regular(values)
    owner = engine.decision().player
    engine = restore(engine, lambda s: s['sheets'][owner].update({area: progress}))
    actions = engine.legal_actions(owner)
    before = engine.snapshot()
    labels = get_game('dice_circuit').describe_actions(actions, engine.observe(owner))
    selected = next(a for a in actions if a.params.get('area') == area and a.params.get('die_id') == area)
    assert selected.params['target'] == target
    label = labels[actions.index(selected)]
    for phrase in phrases:
        assert phrase.casefold() in label.casefold()
    assert engine.snapshot() == before


def test_white_and_bonus_labels_use_current_values_and_selected_bonus():
    engine = regular([1, 1, 1, 2, 1, 5])
    owner = engine.decision().player
    engine = restore(engine, lambda s: s['sheets'][owner].update(orange=[1, 2, 3]))
    actions = engine.legal_actions(owner)
    labels = get_game('dice_circuit').describe_actions(actions, engine.observe(owner))
    white = next(a for a in actions if a.params.get('die_id') == 'white' and a.params.get('area') == 'orange')
    assert 'write 10' in labels[actions.index(white)].lower()
    assert 'x2' in labels[actions.index(white)]
    # Orange field 5 awards a yellow cross with multiple targets, so it stays explicit.
    engine = restore(engine, lambda s: s['sheets'][owner].update(orange=[1, 2, 3, 8]))
    act(engine, kind='select_die', die_id='orange', area='orange')
    actions = engine.legal_actions(owner)
    labels = get_game('dice_circuit').describe_actions(actions, engine.observe(owner))
    assert actions and all(a.kind == 'place_bonus' for a in actions)
    assert all('cross' in label.lower() for label in labels)


def test_black_number_bonus_labels_record_six_regardless_of_dice_faces():
    from test_bonus_flow_views import yellow_bonus
    engine, owner = yellow_bonus('round_bonus')
    engine = restore(engine, lambda s: s['sheets'][owner].update(orange=[1, 2, 3]))
    act(engine, player=owner, kind='choose_round_bonus', bonus='black6')
    actions = engine.legal_actions(owner)
    labels = get_game('dice_circuit').describe_actions(actions, engine.observe(owner))
    assert len(actions) == 2
    orange = next(i for i, action in enumerate(actions) if action.params['area'] == 'orange')
    purple = next(i for i, action in enumerate(actions) if action.params['area'] == 'purple')
    assert 'write 12' in labels[orange].lower() and 'x2' in labels[orange]
    assert 'write 6' in labels[purple].lower()


def test_named_colour_scores_keep_the_same_values_and_encoder_order():
    engine = regular([1] * 6)
    owner = engine.decision().player
    engine = restore(engine, lambda s: s['sheets'][owner].update(
        yellow=[[r, c] for r in range(4) for c in range(4)], blue=[2, 3], green=2,
        orange=[4], purple=[5], emblem_count=2))
    observation = engine.observe(owner)
    assert observation.data['score_summary'][owner] == {
        'areas': {'yellow': 60, 'blue': 2, 'green': 3, 'orange': 4, 'purple': 5},
        'emblem_points': 4, 'total': 78}
    encoder = get_game('dice_circuit').encoder()
    field = next(f for f in encoder.encoder_spec()['fields'] if f['name'] == 'seat.0.area_scores')
    flat = encoder.encode(observation)
    np.testing.assert_allclose(flat[field['offset']:field['offset'] + 5],
                               np.array([60, 2, 3, 4, 5]) / field['normalization']['divisor'])


def test_actions_result_and_replay_use_the_shared_named_projections(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 3)
    made = service.create_game(GameConfig('dice_circuit', 'base', 1), ['human'])
    table = made['table_id']
    token = service.join_game(table, 0)['credential']
    service.start_game(table, made['creator_credential'])
    orange = next(a for a in service.get_actions(table, token)['actions'] if a['params'].get('die_id') == 'orange')
    assert 'write ' in orange['label'].lower() and 'x1' in orange['label']
    while service.get_result(table)['result'] is None:
        turn = service.get_actions(table, token)
        service.apply_action(table, token, turn['decision_id'], turn['actions'][0]['id'])
    result = service.get_result(table)['result']
    assert set(result['area_scores'][0]) == {'yellow', 'blue', 'green', 'orange', 'purple'}
    assert sum(result['area_scores'][0].values()) + result['emblem_scores'][0] == result['scores'][0]
    view = service.get_view(table, token)['observation']['data']
    total = service.get_replay(table, token)['total_positions']
    replay = service.get_replay(table, token, total - 1)['observation']['data']
    assert replay['result'] == view['result'] == result
    assert replay['score_summary'][0]['areas'] == result['area_scores'][0]
    module = get_game('dice_circuit')
    assert asdict(module.decode_result(result, 1))['area_scores'][0] == result['area_scores'][0]
    with pytest.raises(ValueError):
        module.decode_result({**result, 'area_scores': [[1, 2, 3, 4, 5]]}, 1)
