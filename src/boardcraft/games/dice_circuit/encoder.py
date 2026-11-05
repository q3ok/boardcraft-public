"""Observation-only float32 features shared by flat and semantic consumers."""
from array import array
import numpy as np
from boardcraft.contracts import Observation
from .action_catalogue import ActionCatalogue, source_ids
from .encoder_layout import BONUSES, KINDS, PHASES, TURN_PHASES, STAGES, ZONES, _layout, encoder_spec
from .randomness import DICE
from .sheet import load_sheet
from .scoring import COLOURS, score_sheet, score_view
from .visible import visible_sheet


class DiceCircuitEncoder(ActionCatalogue):
    # The layout and the action catalogue are module constants: their identities are computed once per process.
    static_layout = ("dice_circuit", "1")

    def __init__(self):
        super().__init__()
        self._spec = _layout()
        self._fields = {f['name']: f for f in self._spec['fields']}
        self._sources = {source: i for i, source in enumerate(source_ids())}
        self._slots = {f['name']: (f['offset'], f['offset'] + f['width']) for f in self._spec['fields']}
        self._divisors = np.ones(self._spec['flat_size'], dtype=np.float32)
        for f in self._spec['fields']:
            self._divisors[f['offset']:f['offset']+f['width']] = np.asarray(f['normalization']['divisor'], dtype=np.float32)
        self._positions = {}
        self._yellow = {f['name']: [tuple(map(int, coordinate.split(':'))) for coordinate in f['vocabulary']]
                        for f in self._spec['fields'] if f['name'].endswith('.yellow')}
        self._blue = {f['name']: [int(value) for value in f['vocabulary']]
                      for f in self._spec['fields'] if f['name'].endswith('.blue')}

    def encoder_spec(self):
        return encoder_spec()

    @staticmethod
    def _validate(observation):
        if not isinstance(observation, Observation) or (observation.game_id, observation.rules_version,
                observation.observation_version) != ('dice_circuit', '1', '1'):
            raise ValueError('Unsupported Dice Circuit observation version')

    def encode(self, observation):
        self._validate(observation)
        data = observation.data
        # Numerators in plain Python; the whole vector is normalized once at the end.
        vector = [0] * self._spec['flat_size']
        slots, positions = self._slots, self._positions
        def put(name, values):
            start, stop = slots[name]
            if type(values) is list:
                if len(values) != stop - start:
                    raise ValueError('Encoded values do not match their field width')
                vector[start:stop] = values
            elif stop - start == 1:
                vector[start] = values
            else:
                vector[start:stop] = [values] * (stop - start)
        def hot(name, value, vocabulary):
            known = positions.get(name)
            if known is None:
                known = positions[name] = {item: i for i, item in enumerate(vocabulary)}
            index = known.get(value)
            if index is not None:
                vector[slots[name][0] + index] = 1
        players = data['players']
        root = observation.viewer if observation.viewer is not None else 0
        relative = lambda seat: (seat-root) % players
        put('global.present', 1)
        put('global.viewer_present', observation.viewer is not None)
        for key in ('players', 'round', 'rounds_total', 'regular_opportunity', 'turn_index'):
            hot('global.' + key, data[key], self._fields['global.' + key]['vocabulary'])
        hot('global.start_player', relative(data['start_player']), range(4))
        put('global.rounds_remaining', data['rounds_remaining'])
        if observation.viewer is not None:
            for key in ('active_turns_left', 'passive_turns_left'):
                put('global.' + key, data[key])
        hot('global.phase', data['phase'], PHASES)
        hot('global.turn_phase', data['turn_phase'], TURN_PHASES)
        hot('global.active', relative(data['active_player']), range(4))
        if observation.decision is not None:
            put('global.decision_present', 1)
            hot('global.decision_owner', relative(observation.decision.player), range(4))
            hot('global.decision_kind', observation.decision.kind, KINDS)
        window = data.get('window')
        if window is not None:
            put('global.window_present', 1)
            hot('global.stage', window['stage'], STAGES)
        for die in DICE:
            prefix = f'die.{die}.'
            put(prefix + 'present', 1)
            hot(prefix + 'value', data['dice'][die]['value'], range(1, 7))
            hot(prefix + 'zone', data['dice'][die]['zone'], ZONES)
            put(prefix + 'extra_used', window is not None and die in window['extra_used'])
        for relative_seat in range(players):
            sheet_data = data['sheets'][(root + relative_seat) % players]
            sheet = visible_sheet(sheet_data)
            prefix = f'seat.{relative_seat}.'
            put(prefix + 'present', 1)
            put(prefix + 'active_done_this_round', sheet_data['active_done_this_round'])
            hot(prefix + 'seat_offset_from_active', sheet_data['seat_offset_from_active'], range(4))
            put(prefix + 'yellow', [coordinate in sheet.yellow for coordinate in self._yellow[prefix+'yellow']])
            put(prefix + 'blue', [value in sheet.blue for value in self._blue[prefix+'blue']])
            put(prefix + 'green', [i < sheet.green for i in range(self._fields[prefix+'green']['width'])])
            for area in ('orange', 'purple'):
                values = getattr(sheet, area)
                length = self._fields[prefix+area]['shape'][0]
                put(prefix + area + '_present', [i < len(values) for i in range(length)])
                if area == 'orange':
                    values = [value // multiplier for value, multiplier in zip(values, load_sheet().orange.multipliers)]
                padded = values + [0] * (length - len(values))
                put(prefix + area, [value == category for value in padded for category in range(7)])
            put(prefix + 'earned', [source in sheet.earned_rewards for source in self._sources])
            for resource in ('rerolls', 'extras'):
                for status in ('earned', 'used'):
                    name = f'{resource}_{status}'
                    put(prefix + name, [i < sheet_data[name] for i in range(load_sheet().resource_slots)])
            put(prefix + 'emblem_count', sheet.emblems)
            scores = score_view(score_sheet(sheet))
            put(prefix + 'area_scores', [scores['areas'][colour] for colour in COLOURS])
            put(prefix + 'emblem_points', scores['emblem_points'])
            put(prefix + 'total_score', scores['total'])
        def ancestry(prefix, items):
            ranks = {source: rank+1 for rank, source in enumerate(items)}
            put(prefix + 'ancestry_present', [source in ranks for source in self._sources])
            put(prefix + 'ancestry_rank', [ranks.get(source, 0) for source in self._sources])
        if window is not None:
            stack = window['bonus_stack']
            if len(stack['frames']) > self._spec['max_frames']:
                raise ValueError('Bonus frames exceed the finite printed source catalogue')
            for i, frame in enumerate(stack['frames']):
                prefix = f'frame.{i}.'
                put(prefix + 'present', 1)
                put(prefix + 'locked', i != len(stack['frames'])-1 or stack['current'] is not None)
                put(prefix + 'top', i == len(stack['frames'])-1)
                pending = {reward['source_id']: reward['kind'] for reward in frame['pending']}
                put(prefix + 'pending_present', [source in pending for source in self._sources])
                put(prefix + 'pending_kind', [BONUSES.index(pending[source])+1 if source in pending else 0 for source in self._sources])
                ancestry(prefix, frame['ancestry'])
            current = stack['current']
            if current is not None:
                put('current.present', 1)
                hot('current.source', current['id'], self._sources)
                hot('current.kind', current['kind'], BONUSES)
                ancestry('current.', current['ancestry'])
        return np.frombuffer(array('f', vector), dtype=np.float32) / self._divisors

    def mask(self, observation, legal_actions):
        self._validate(observation)
        result = np.zeros(len(self._actions), dtype=np.bool_)
        for action in legal_actions:
            result[self.encode_action(action)] = True
        return result
