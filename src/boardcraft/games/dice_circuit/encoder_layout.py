"""One semantic layout for flat features and derived entity index maps."""
from boardcraft.contracts import _copy_json
from functools import lru_cache
from .action_catalogue import entry_source_ids, source_ids
from .actions import reward_catalog
from .randomness import DICE
from .sheet import load_sheet

TURN_PHASES = ('regular', 'active_extra', 'passive', 'round_bonus', 'over')
PHASES = (*TURN_PHASES, 'bonus')
STAGES = ('done', 'regular', 'extra', 'passive', 'round', 'after_regular')
KINDS = ('select_die', 'extra_die', 'place_bonus', 'choose_bonus', 'choose_round_bonus',
         'reroll', 'finish_extra', 'decline_reroll')
BONUSES = tuple(dict.fromkeys(r.kind for r in reward_catalog() if r.kind not in ('reroll', 'extra', 'endgame')))
ZONES = ('available', 'kept', 'tray')


def score_bounds():
    """Theoretical upper bounds, without asserting simultaneous attainability."""
    s = load_sheet()
    areas = (sum(s.yellow.column_points), max(s.blue.points_by_count), max(s.green.points_by_count),
             6 * sum(s.orange.multipliers), 6 * s.purple.length)
    emblems = len({r.source_id for r in reward_catalog() if r.kind == 'endgame'})
    return areas, emblems * min(areas), sum(areas) + emblems * min(areas)


@lru_cache(maxsize=1)
def _layout():
    spec, sources = load_sheet(), source_ids()
    depth = len(entry_source_ids())
    fields = []
    def add(group, entity, key, width=1, scale=1, kind='boolean', vocabulary=None, presence=None):
        name = f'{entity}.{key}'
        offset = sum(f['width'] for f in fields)
        item = {'name': name, 'offset': offset, 'width': width, 'shape': [width],
                'value_kind': kind, 'normalization': {'divisor': scale, 'clipped': False},
                'raw_bounds': {'minimum': 0, 'theoretical_maximum': scale},
                'encoding': {'boolean': 'indicator', 'continuous': 'divide_by_divisor',
                             'categorical': 'one_based_vocabulary_index_divided_by_divisor',
                             'ordinal': 'one_based_ancestor_rank_divided_by_divisor',
                             'one_hot': 'one_hot', 'thermometer': 'thermometer'}[kind],
                'group': group, 'entity': entity,
                'presence_field': presence or f'{entity}.present', 'padding_value': 0}
        if vocabulary is not None:
            item['vocabulary'] = list(vocabulary)
        fields.append(item)
    for key in ('present', 'viewer_present'):
        add('global', 'global', key)
    for key, vocabulary in (('players', range(1, 5)), ('round', range(1, 7)), ('rounds_total', (4, 5, 6))):
        add('global', 'global', key, len(vocabulary), kind='one_hot', vocabulary=vocabulary)
    for key, scale in (('rounds_remaining', 6), ('active_turns_left', 6), ('passive_turns_left', 12)):
        add('global', 'global', key, scale=scale, kind='continuous',
            presence='global.viewer_present' if key.endswith('turns_left') else None)
    for key in ('turn_index', 'start_player'):
        add('global', 'global', key, 4, kind='one_hot', vocabulary=range(4))
    add('global', 'global', 'regular_opportunity', 3, kind='one_hot', vocabulary=range(1, 4))
    for key, vocabulary in (('phase', PHASES), ('turn_phase', TURN_PHASES), ('active', range(4))):
        add('global', 'global', key, len(vocabulary), vocabulary=vocabulary)
    add('global', 'global', 'decision_present')
    add('global', 'global', 'decision_owner', 4, vocabulary=range(4), presence='global.decision_present')
    add('global', 'global', 'decision_kind', len(KINDS), vocabulary=KINDS, presence='global.decision_present')
    add('global', 'global', 'window_present')
    add('global', 'global', 'stage', len(STAGES), vocabulary=STAGES, presence='global.window_present')
    for die in DICE:
        entity = f'die.{die}'
        add('dice', entity, 'present')
        add('dice', entity, 'value', 6, kind='one_hot', vocabulary=range(1, 7))
        add('dice', entity, 'zone', len(ZONES), vocabulary=ZONES)
        add('dice', entity, 'extra_used', presence='global.window_present')
    area_bounds, emblem_bound, total_bound = score_bounds()
    for seat in range(4):
        entity = f'seat.{seat}'
        add('seats', entity, 'present')
        add('seats', entity, 'active_done_this_round')
        add('seats', entity, 'seat_offset_from_active', 4, kind='one_hot', vocabulary=range(4))
        add('seats', entity, 'yellow', sum(len(row) for row in spec.yellow.rows), vocabulary=[f'{r}:{c}' for r,row in enumerate(spec.yellow.rows) for c in range(len(row))])
        fields[-1]['shape'] = [len(spec.yellow.rows), len(spec.yellow.rows[0])]
        add('seats', entity, 'blue', len(spec.blue.values), vocabulary=sorted(spec.blue.values))
        add('seats', entity, 'green', len(spec.green.thresholds), vocabulary=range(len(spec.green.thresholds)))
        for area, length in (('orange', len(spec.orange.multipliers)), ('purple', spec.purple.length)):
            add('seats', entity, area + '_present', length, vocabulary=range(length))
            add('seats', entity, area, length * 7, kind='one_hot', vocabulary=range(7))
            fields[-1]['shape'] = [length, 7]
            fields[-1]['axis_ids'] = list(range(length))
            fields[-1]['value_semantics'] = ('entered face before printed multiplier; zero means unwritten'
                                            if area == 'orange' else 'written value; zero means unwritten')
        add('seats', entity, 'earned', len(sources), vocabulary=sources)
        for resource in ('rerolls', 'extras'):
            for status in ('earned', 'used'):
                add('seats', entity, f'{resource}_{status}', spec.resource_slots, kind='thermometer',
                    vocabulary=range(spec.resource_slots))
        bound = len({r.source_id for r in reward_catalog() if r.kind == 'endgame'})
        add('seats', entity, 'emblem_count', scale=bound, kind='continuous')
        add('seats', entity, 'area_scores', 5, scale=list(area_bounds), kind='continuous', vocabulary=('yellow', 'blue', 'green', 'orange', 'purple'))
        add('seats', entity, 'emblem_points', scale=emblem_bound, kind='continuous')
        add('seats', entity, 'total_score', scale=total_bound, kind='continuous')
    for index in range(depth):
        entity = f'frame.{index}'
        for key in ('present', 'locked', 'top'):
            add('frames', entity, key)
        add('frames', entity, 'pending_present', len(sources), vocabulary=sources)
        add('frames', entity, 'pending_kind', len(sources), len(BONUSES), 'categorical', vocabulary=BONUSES, presence=f'{entity}.pending_present')
        fields[-1]['axis_ids'] = list(sources)
        add('frames', entity, 'ancestry_present', len(sources), vocabulary=sources)
        add('frames', entity, 'ancestry_rank', len(sources), depth, 'ordinal', vocabulary=sources, presence=f'{entity}.ancestry_present')
    add('current', 'current', 'present')
    add('current', 'current', 'source', len(sources), vocabulary=sources)
    add('current', 'current', 'kind', len(BONUSES), vocabulary=BONUSES)
    add('current', 'current', 'ancestry_present', len(sources), vocabulary=sources)
    add('current', 'current', 'ancestry_rank', len(sources), depth, 'ordinal', vocabulary=sources, presence='current.ancestry_present')
    groups = []
    for group_id in dict.fromkeys(f['group'] for f in fields):
        entities = list(dict.fromkeys(f['entity'] for f in fields if f['group'] == group_id))
        groups.append({'id': group_id, 'entity_kind': group_id, 'entity_ids': entities,
                       'feature_indices': [[i for f in fields if f['group'] == group_id and f['entity'] == e
                                            for i in range(f['offset'], f['offset']+f['width'])] for e in entities],
                       'presence_indices': [next(f['offset'] for f in fields if f['name'] == f'{e}.present') for e in entities],
                       'padding_value': 0})
    return {'game_id': 'dice_circuit', 'encoder_id': 'dice-circuit-visible', 'version': '1',
            'observation_version': '1', 'dtype': 'float32', 'flat_size': sum(f['width'] for f in fields),
            'seat_order': 'viewer then cyclic seats; observer starts at seat zero',
            'frame_order': 'root to leaf; pending siblings are locked in ancestor frames and while a current bonus is selected',
            'max_frames': depth, 'source_ids': list(sources), 'bonus_kinds': list(BONUSES),
            'score_bounds': 'theoretical upper bounds', 'fields': fields, 'groups': groups}


def encoder_spec():
    return _copy_json(_layout())
