"""Visible-state strategy evaluation over the canonical sheet and bonus rules."""
from functools import lru_cache
import math

from .actions import Reward, apply_entry, award_rewards, bonus_entries, legal_entries
from .bonuses import BonusStack
from .scoring import score_sheet
from .sheet import load_sheet
from .visible import visible_sheet


def sheet_key(sheet):
    return (frozenset(sheet.yellow), frozenset(sheet.blue), sheet.green,
            tuple(sheet.orange), tuple(sheet.purple), frozenset(sheet.earned_rewards),
            sheet.rerolls, sheet.extras, sheet.emblems)


@lru_cache(maxsize=1)
def goals():
    """Printed goal identities and fields; values are evaluated on the live sheet."""
    spec = load_sheet()
    result = []
    for col, points in enumerate(spec.yellow.column_points):
        result.append(('yellow', tuple((r, col) for r in range(4)), '', points))
    for row, kind in enumerate(spec.yellow.row_bonuses):
        result.append(('yellow', tuple((row, c) for c in range(4)), f'yellow:row:{row}', kind))
    result.append(('yellow', tuple((i, i) for i in range(4)), 'yellow:diagonal', spec.yellow.main_diagonal_bonus))
    for row, kind in enumerate(spec.blue.row_bonuses):
        result.append(('blue', tuple(v for v in spec.blue.rows[row] if v is not None), f'blue:row:{row}', kind))
    for col, kind in enumerate(spec.blue.column_bonuses):
        result.append(('blue', tuple(row[col] for row in spec.blue.rows if row[col] is not None),
                       f'blue:column:{col}', kind))
    for area in ('green', 'orange', 'purple'):
        for position, kind in getattr(spec, area).bonuses_by_position:
            result.append((area, position, f'{area}:position:{position}', kind))
    return tuple(result)


class Evaluation:
    """One decision's detached sheet, fixed horizon, and memoized exact chains.

    No engine snapshot or engine random stream is needed. Only the owner's
    projected stack is restored, including its visible locked siblings.
    """

    def __init__(self, observation, policy):
        self.data, self.policy = observation.data, policy
        self.sheet = visible_sheet(self.data['sheets'][observation.viewer])
        self.spec = load_sheet()
        self.active = self.data['turn_phase'] == 'regular'
        self.roll = self.data['regular_opportunity']
        active_left, passive_left = self.data['active_turns_left'], self.data['passive_turns_left']
        current_active = self.data['active_player'] == observation.viewer and self.data['turn_phase'] in ('regular', 'active_extra')
        self.regular_left = 3 * max(0, active_left - int(current_active))
        if current_active and self.active:
            self.regular_left += max(0, 4 - self.roll)
        self.passive_left = passive_left
        if self.data['turn_phase'] == 'passive' and self.data['window']['stage'] != 'passive':
            self.passive_left = max(0, self.passive_left - 1)
        # The final multiplayer active seat has no later passive window. Treat
        # its third selection and extra window like the final solo passive one.
        final_passive = active_left == 0 and passive_left <= 1 and self.data['turn_phase'] == 'passive'
        final_active = (current_active and active_left == 1 and passive_left == 0
                        and (self.data['turn_phase'] == 'active_extra' or self.roll == 3))
        self.final = final_passive or final_active
        self.time = 0. if self.final else min(1., (self.regular_left + self.passive_left) / 8.)
        self.faces = {die: item['value'] for die, item in self.data['dice'].items()}
        self.available = [die for die, item in self.data['dice'].items() if item['zone'] == 'available']
        saved = self.data['window']['bonus_stack']
        self.stack = BonusStack.restore(dict(version=1, frames=saved['frames'], current=saved['current']))
        self._values, self._chains = {}, {}

    def extra_reserve(self):
        if self.final:
            return 0.
        if self.data['active_turns_left'] <= 1 and self.data['rounds_remaining'] == 0:
            return 2.
        return (7., 7., 7., 6., 4., 2.)[min(5, self.data['round'] - 1)]

    def reward_value(self, kind, sheet, forecast):
        if isinstance(kind, int):
            return kind
        if kind == 'endgame':
            return forecast
        if kind == 'extra':
            return 7.
        if kind == 'reroll':
            return 2.5 if self.regular_left else 0.
        if kind.endswith('X'):
            return 7.
        entries = bonus_entries(sheet, kind)
        if not entries:
            return 0.
        if kind.startswith('orange'):
            return int(kind[-1]) * self.spec.orange.multipliers[len(sheet.orange)]
        return float(kind[-1])

    def state_value(self, sheet):
        key = sheet_key(sheet)
        if key in self._values:
            return self._values[key]
        score = score_sheet(sheet)
        if self.final:
            return float(score.total)
        p, spec = self.policy, self.spec
        expected = p.area_share * (self.regular_left + self.passive_left) + .4 * sheet.extras
        capacities = (len(spec.yellow.rows) * len(spec.yellow.rows[0]) - len(sheet.yellow),
                      len(spec.blue.values) - len(sheet.blue), len(spec.green.thresholds) - sheet.green,
                      len(spec.orange.multipliers) - len(sheet.orange), spec.purple.length - len(sheet.purple))
        gains = (4., 5., float(sheet.green + 1),
                 3.5 * (spec.orange.multipliers[len(sheet.orange)] if capacities[3] else 0), 3.5)
        ceilings = (sum(spec.yellow.column_points), spec.blue.points_by_count[-1], spec.green.points_by_count[-1],
                    score.orange + 6 * sum(spec.orange.multipliers[len(sheet.orange):]),
                    score.purple + 6 * capacities[4])
        forecast = min(min(ceiling, area + min(expected, capacity) * gain)
                       for area, gain, capacity, ceiling in zip(score.areas, gains, capacities, ceilings))
        potential = 0.
        for area, fields, source, reward in goals():
            if source in sheet.earned_rewards:
                continue
            if area in ('yellow', 'blue'):
                marked = getattr(sheet, area)
                missing = sum(field not in marked for field in fields)
            else:
                count = sheet.green if area == 'green' else len(getattr(sheet, area))
                missing = fields - count
            if missing > 0:
                potential += self.reward_value(reward, sheet, forecast) * p.goal_discount ** missing * min(1., expected / missing)
        floor = (0, 3, 8, 15, 20, 25)[min(5, self.data['round'] - 1)]
        balance = p.balance_weight * sum(min(area, floor) for area in score.areas)
        last = sheet.purple[-1] if sheet.purple else 6
        flexibility = p.purple_weight * (6 if last == 6 else 6 - last) * self.time if capacities[4] else 0.
        # Rerolls have one cost in their separate decision branch. Their inventory
        # value only rewards newly earned rerolls during an entry or bonus chain.
        reserve = (2.5 if self.data['round'] <= 3 else 1.5)
        if self.data['rounds_remaining'] == 0:
            reserve = .5
        if not self.regular_left:
            reserve = 0.
        resources = sheet.extras * self.extra_reserve() + reserve * sheet.rerolls
        value = score.total + potential + balance + flexibility + resources
        self._values[key] = value
        return value

    def resolve(self, sheet, stack):
        """Maximize final value over every legal target and depth-first ordering."""
        saved = stack.snapshot()
        stack_key = (tuple((tuple((r['source_id'], r['kind']) for r in f['pending']), tuple(f['ancestry']))
                           for f in saved['frames']),
                     None if stack.current is None else (stack.current.id, stack.current.kind, stack.current.ancestry))
        key = (sheet_key(sheet), stack_key)
        if key in self._chains:
            return self._chains[key]
        if stack.is_empty:
            result = (self.state_value(sheet), 0)
        elif stack.current is None:
            branches = []
            for choice in stack.available_choices():
                branch = stack.copy()
                branch.select(choice.id)
                branches.append(self.resolve(sheet, branch))
            result = max(branches, key=lambda pair: pair[0] + self.policy.chain_weight * pair[1] * self.time)
        else:
            entries = bonus_entries(sheet, stack.current.kind)
            if not entries:
                branch = stack.copy()
                branch.expire_current(sheet)
                result = self.resolve(sheet, branch)
            else:
                branches = []
                for entry in entries:
                    after, branch = sheet.copy(), stack.copy()
                    branch.place(after, entry)
                    value, count = self.resolve(after, branch)
                    branches.append((value, count + 1))
                result = max(branches, key=lambda pair: pair[0] + self.policy.chain_weight * pair[1] * self.time)
        self._chains[key] = result
        return result

    def dice_loss(self, selected):
        if not self.active or selected.kind != 'select_die':
            return 0.
        die = selected.params['die_id']
        survivors = sum(d != die and self.faces[d] >= self.faces[die] for d in self.available)
        k = max(0, 3 - self.roll)
        def retention(n):
            return self.policy.opportunity_value * sum(1 - (1 - .55) ** max(0, n - j + 1) for j in range(1, k + 1))
        return retention(len(self.available) - 1) - retention(survivors)

    def move_value(self, selected):
        if selected.kind == 'finish_extra':
            return 0.
        if selected.kind == 'decline_reroll':
            return 0.
        after, stack = self.sheet.copy(), self.stack.copy()
        if selected.kind == 'choose_bonus':
            stack.select(selected.params['bonus_id'])
        elif selected.kind == 'choose_round_bonus':
            stack.start(award_rewards(after, (Reward(f"round:{self.data['round']}", selected.params['bonus']),)))
        else:
            params = selected.params
            entries = bonus_entries(after, stack.current.kind) if selected.kind == 'place_bonus' else legal_entries(after, params['die_id'], self.faces)
            target = tuple(params['target']) if params['area'] == 'yellow' else params['target']
            entry = next(e for e in entries if e.area == params['area'] and e.target == target)
            if selected.kind == 'place_bonus':
                stack.place(after, entry)
            else:
                stack.start(apply_entry(after, entry))
            if selected.kind == 'extra_die':
                after.extras -= 1
                after.extras_used += 1
        value, count = self.resolve(after, stack)
        return value - self.state_value(self.sheet) - self.dice_loss(selected) + self.policy.chain_weight * count * self.time

    def reroll_threshold(self):
        if self.data['rounds_remaining'] == 0:
            threshold = 9.
        else:
            threshold = 5. if self.data['round'] <= 3 else 7.
        # The fallback thresholds assume six dice. Fewer faces offer fewer ways
        # to improve. Multiple resources reduce the cost of trying again.
        reservation = (2.5 if self.data['round'] <= 3 else 1.5) if self.data['rounds_remaining'] else .5
        discount = reservation * (1 - 1 / math.sqrt(max(1, self.sheet.rerolls)))
        return threshold * math.sqrt(len(self.available) / 6) + discount
