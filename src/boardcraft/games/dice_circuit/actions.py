"""Sheet entries and English descriptions of semantic game actions."""
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Mapping

from boardcraft.contracts import Action, IllegalAction
from .scoring import orange_recorded_value
from .sheet import load_sheet
from .state import PlayerSheet


@dataclass(frozen=True)
class EntryTarget:
    area: str
    target: tuple[int, int] | int
    value: int | None


@dataclass(frozen=True)
class Reward:
    source_id: str
    kind: str


@lru_cache(maxsize=1)
def reward_catalog() -> tuple[Reward, ...]:
    """Finite reward identities derived from the one printed-sheet resource."""
    spec = load_sheet()
    rewards = [Reward(f'yellow:row:{row}', kind) for row, kind in enumerate(spec.yellow.row_bonuses)]
    rewards.append(Reward('yellow:diagonal', spec.yellow.main_diagonal_bonus))
    rewards.extend(Reward(f'blue:row:{row}', kind) for row, kind in enumerate(spec.blue.row_bonuses))
    rewards.extend(Reward(f'blue:column:{column}', kind) for column, kind in enumerate(spec.blue.column_bonuses))
    for area in ('green', 'orange', 'purple'):
        rewards.extend(Reward(f'{area}:position:{position}', kind)
                       for position, kind in getattr(spec, area).bonuses_by_position)
    for round_number, kind in enumerate(spec.round_rewards, 1):
        if kind is not None:
            rewards.extend(Reward(f'round:{round_number}', choice)
                           for choice in (('blackX', 'black6') if kind == 'blackX-or-black6' else (kind,)))
    return tuple(rewards)


def award_rewards(sheet: PlayerSheet, rewards: Iterable[Reward]) -> tuple[Reward, ...]:
    """Grant canonical sheet/round sources once, including immediate leaves."""
    items = tuple(rewards)
    catalog = reward_catalog()
    if any(reward not in catalog for reward in items) or len({reward.source_id for reward in items}) != len(items):
        raise IllegalAction('Rewards must have distinct printed source identities')
    granted = tuple(reward for reward in items if reward.source_id not in sheet.earned_rewards)
    for reward in granted:
        sheet.earned_rewards.add(reward.source_id)
        if reward.kind == 'reroll':
            sheet.rerolls += 1
        elif reward.kind == 'extra':
            sheet.extras += 1
        elif reward.kind == 'endgame':
            sheet.emblems += 1
    return granted


def _targets(sheet: PlayerSheet, area: str, value: int | None) -> tuple[EntryTarget, ...]:
    spec = load_sheet()
    if area == 'yellow':
        return tuple(EntryTarget(area, (row, column), value)
                     for row, values in enumerate(spec.yellow.rows) for column, printed in enumerate(values)
                     if (row, column) not in sheet.yellow and printed is not None and (value is None or value == printed))
    if area == 'blue':
        return tuple(EntryTarget(area, number, value) for number in sorted(spec.blue.values - sheet.blue)
                     if value is None or value == number)
    if area == 'green':
        position = sheet.green
        if position < len(spec.green.thresholds) and (value is None or value >= spec.green.thresholds[position]):
            return (EntryTarget(area, position, value),)
    if area == 'orange' and value is not None and len(sheet.orange) < len(spec.orange.multipliers):
        return (EntryTarget(area, len(sheet.orange), value),)
    if area == 'purple' and value is not None and len(sheet.purple) < spec.purple.length:
        if not sheet.purple or sheet.purple[-1] == 6 or value > sheet.purple[-1]:
            return (EntryTarget(area, len(sheet.purple), value),)
    return ()


def legal_entries(sheet: PlayerSheet, die_id: str, dice_values: Mapping[str, int]) -> tuple[EntryTarget, ...]:
    """Use current faces only; dice availability and locations belong to the turn."""
    if die_id not in ('yellow', 'blue', 'green', 'orange', 'purple', 'white'):
        raise IllegalAction('Unknown die')
    required = {die_id} | ({'blue', 'white'} if die_id in ('blue', 'white') else set())
    if any(type(dice_values.get(die)) is not int or not 1 <= dice_values[die] <= 6 for die in required):
        raise IllegalAction('Required dice must have integer values from 1 to 6')
    areas = ('yellow', 'blue', 'green', 'orange', 'purple') if die_id == 'white' else (die_id,)
    return tuple(entry for area in areas for entry in _targets(
        sheet, area, dice_values['blue'] + dice_values['white'] if area == 'blue' else dice_values[die_id]))


def bonus_entries(sheet: PlayerSheet, bonus: str) -> tuple[EntryTarget, ...]:
    """Crosses bypass printed numbers and thresholds; number bonuses stay legal."""
    if bonus == 'blackX':
        return tuple(entry for area in ('yellow', 'blue', 'green') for entry in _targets(sheet, area, None))
    if bonus == 'black6':
        return _targets(sheet, 'orange', 6) + _targets(sheet, 'purple', 6)
    if bonus in ('yellowX', 'blueX', 'greenX'):
        return _targets(sheet, bonus[:-1], None)
    if bonus in ('orange4', 'orange5', 'orange6', 'purple6'):
        return _targets(sheet, bonus[:-1], int(bonus[-1]))
    raise IllegalAction('Unknown entry bonus')


def _completed_sources(sheet: PlayerSheet) -> set[str]:
    spec = load_sheet()
    sources = {f'yellow:row:{row}' for row, values in enumerate(spec.yellow.rows)
               if all((row, column) in sheet.yellow for column in range(len(values)))}
    if all((index, index) in sheet.yellow for index in range(len(spec.yellow.rows))):
        sources.add('yellow:diagonal')
    for row, values in enumerate(spec.blue.rows):
        if all(value in sheet.blue for value in values if value is not None):
            sources.add(f'blue:row:{row}')
    for column in range(len(spec.blue.rows[0])):
        if all(row[column] in sheet.blue for row in spec.blue.rows if row[column] is not None):
            sources.add(f'blue:column:{column}')
    for area, count in (('green', sheet.green), ('orange', len(sheet.orange)), ('purple', len(sheet.purple))):
        sources.update(f'{area}:position:{position}' for position, _ in getattr(spec, area).bonuses_by_position
                       if position <= count)
    return sources


def apply_entry(sheet: PlayerSheet, entry: EntryTarget) -> tuple[Reward, ...]:
    """Validate before mutation, record one field, and grant newly earned rewards."""
    if not isinstance(entry, EntryTarget):
        raise IllegalAction('An entry target is required')
    valid_target = (isinstance(entry.target, tuple) and len(entry.target) == 2
                    and all(type(value) is int for value in entry.target)) if entry.area == 'yellow' else type(entry.target) is int
    limit = 12 if entry.area == 'blue' else 6
    valid_value = entry.value is None or (type(entry.value) is int and 1 <= entry.value <= limit)
    if not valid_target or not valid_value or entry not in _targets(sheet, entry.area, entry.value):
        raise IllegalAction('The entry is not legal')
    before = _completed_sources(sheet)
    if entry.area == 'yellow':
        sheet.yellow.add(entry.target)
    elif entry.area == 'blue':
        sheet.blue.add(entry.target)
    elif entry.area == 'green':
        sheet.green += 1
    elif entry.area == 'orange':
        sheet.orange.append(orange_recorded_value(entry.value, entry.target))
    else:
        sheet.purple.append(entry.value)
    new_sources = _completed_sources(sheet) - before
    if not new_sources:
        return ()
    return award_rewards(sheet, (reward for reward in reward_catalog() if reward.source_id in new_sources))


def entry_from_observation(observation, selected: Action) -> EntryTarget:
    """Reconstruct a visible entry for descriptions and the baseline heuristic."""
    from .visible import visible_sheet
    params = selected.params
    sheet = visible_sheet(observation.data['sheets'][observation.viewer])
    if selected.kind == 'place_bonus':
        entries = bonus_entries(sheet, observation.data['window']['bonus_stack']['current']['kind'])
    else:
        entries = legal_entries(sheet, params['die_id'], {die: item['value'] for die, item in observation.data['dice'].items()})
    return next(entry for entry in entries if entry.area == params['area'] and
                (list(entry.target) if isinstance(entry.target, tuple) else entry.target) == params['target'])


def _entry_description(action, observation):
    from .visible import visible_sheet
    entry = entry_from_observation(observation, action)
    if entry.area == 'orange':
        multiplier = load_sheet().orange.multipliers[entry.target]
        text = f'Write {orange_recorded_value(entry.value, entry.target)} ({entry.value} x{multiplier} multiplier).'
    elif entry.area == 'purple':
        text = f'Write {entry.value}.'
    else:
        text = 'Mark a cross.'
    preview = visible_sheet(observation.data['sheets'][observation.viewer])
    rewards = apply_entry(preview, entry)
    if rewards:
        names = {'reroll': 'reroll', 'extra': 'extra die +1 (extra)', 'endgame': 'endgame emblem',
                 'yellowX': 'yellow cross (yellowX)', 'blueX': 'blue cross (blueX)', 'greenX': 'green cross (greenX)',
                 'orange4': 'orange 4 (orange4)', 'orange5': 'orange 5 (orange5)', 'orange6': 'orange 6 (orange6)',
                 'purple6': 'purple 6 (purple6)'}
        text += ' Gain: ' + ', '.join(names[reward.kind] for reward in rewards) + '.'
    return text


def describe_action(action: Action, observation=None) -> str:
    params = action.params
    if action.kind in ('select_die', 'extra_die', 'place_bonus'):
        area, target = params['area'], params['target']
        if area == 'yellow':
            location = f'row {target[0] + 1}, column {target[1] + 1}'
        elif area == 'blue':
            location = f'value {target}'
        else:
            location = f'field {target + 1}'
        if area != 'blue':
            location += ' (1-based; target indices are 0-based)'
        if action.kind == 'place_bonus':
            label = f'Place the bonus in {area} at {location}.'
        else:
            prefix = 'Use an extra' if action.kind == 'extra_die' else 'Use'
            label = f"{prefix} {params['die_id']} die in {area} at {location}."
        return label if observation is None else label + ' ' + _entry_description(action, observation)
    descriptions = {
        'reroll': 'Reroll all available dice.',
        'finish_extra': 'Finish extra-die choices.',
        'decline_reroll': 'Continue without a reroll.',
    }
    if action.kind in descriptions:
        return descriptions[action.kind]
    if action.kind == 'choose_bonus':
        return f"Resolve bonus {params['bonus_id']}."
    if action.kind == 'choose_round_bonus':
        return {'blackX': 'Choose the black cross bonus.', 'black6': 'Choose the black 6 bonus.'}[params['bonus']]
    raise ValueError(f'Unknown action kind: {action.kind}')
