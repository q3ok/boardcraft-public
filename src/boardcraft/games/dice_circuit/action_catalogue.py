"""Version-one action ordering, built with the engine's semantic ID builder."""
from functools import lru_cache
import json
from boardcraft.contracts import Action
from .actions import EntryTarget, reward_catalog
from .randomness import DICE
from .sheet import load_sheet
from .turn_actions import action, entry_action


@lru_cache(maxsize=1)
def targets():
    spec = load_sheet()
    entries = [EntryTarget('yellow', (r, c), None) for r, row in enumerate(spec.yellow.rows)
               for c, value in enumerate(row) if value is not None]
    entries += [EntryTarget('blue', value, None) for value in sorted(spec.blue.values)]
    for area, length in (('green', len(spec.green.thresholds)), ('orange', len(spec.orange.multipliers)),
                         ('purple', spec.purple.length)):
        entries += [EntryTarget(area, i, None) for i in range(length)]
    return tuple(entries)


@lru_cache(maxsize=1)
def source_ids():
    return tuple(dict.fromkeys(reward.source_id for reward in reward_catalog()))


@lru_cache(maxsize=1)
def entry_source_ids():
    return tuple(dict.fromkeys(r.source_id for r in reward_catalog() if r.kind not in ('reroll', 'extra', 'endgame')))


@lru_cache(maxsize=1)
def _catalogue():
    entries = targets()
    result = [entry_action(kind, entry, die) for kind in ('select_die', 'extra_die')
              for die in DICE for entry in entries if die == 'white' or die == entry.area]
    result += [entry_action('place_bonus', entry) for entry in entries]
    result += [action('choose_bonus', bonus_id=source) for source in entry_source_ids()]
    result += [action('choose_round_bonus', bonus=kind) for kind in ('blackX', 'black6')]
    result += [action(kind) for kind in ('reroll', 'finish_extra', 'decline_reroll')]
    return tuple(result)


class ActionCatalogue:
    def __init__(self):
        self._actions = _catalogue()
        self._indices = {a.id: i for i, a in enumerate(self._actions)}
        self._params = [json.dumps(a.params, sort_keys=True) for a in self._actions]

    def encode_action(self, selected: Action) -> int:
        index = self._indices.get(selected.id) if isinstance(selected, Action) else None
        if index is None or selected.kind != self._actions[index].kind or json.dumps(selected.params, sort_keys=True) != self._params[index]:
            raise ValueError('Action is outside the version-one catalogue')
        return index

    def decode_action(self, index: int) -> Action:
        if type(index) is not int or not 0 <= index < len(self._actions):
            raise ValueError('Action index is outside the version-one catalogue')
        item = self._actions[index]
        return Action(item.id, item.kind, item.params)

    def action_spec(self):
        return {'game_id': 'dice_circuit', 'action_id': 'dice-circuit-actions', 'version': '1',
                'size': len(self._actions), 'actions': [
                    {'index': i, 'id': a.id, 'kind': a.kind, 'params': dict(self.decode_action(i).params)}
                    for i, a in enumerate(self._actions)]}
