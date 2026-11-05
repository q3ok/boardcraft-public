"""Serializable depth-first entry-bonus branches, independent of turn flow."""
from dataclasses import dataclass
from typing import Iterable

from boardcraft.contracts import IllegalAction, JsonValue
from .actions import EntryTarget, Reward, apply_entry, bonus_entries, reward_catalog
from .state import PlayerSheet


@dataclass(frozen=True)
class BonusChoice:
    id: str
    kind: str
    ancestry: tuple[str, ...]


@dataclass
class _Frame:
    pending: list[Reward]
    ancestry: tuple[str, ...]


def _entry_rewards(rewards: Iterable[Reward]) -> list[Reward]:
    items = list(rewards)
    catalog = reward_catalog()
    if any(reward not in catalog for reward in items) or len({reward.source_id for reward in items}) != len(items):
        raise IllegalAction('Rewards must have distinct printed source identities')
    return [reward for reward in items if reward.kind not in ('reroll', 'extra', 'endgame')]


class BonusStack:
    """One owner's private chain; the engine controls its public projection.

    Resource and emblem leaves have already been granted by apply_entry. Only
    entry rewards become choices. No turn action is represented by this stack.
    """

    def __init__(self, rewards: Iterable[Reward] = ()) -> None:
        self._frames: list[_Frame] = []
        self._current: BonusChoice | None = None
        self.start(rewards)

    @property
    def current(self) -> BonusChoice | None:
        return self._current

    @property
    def is_empty(self) -> bool:
        return not self._frames and self._current is None

    def start(self, rewards: Iterable[Reward]) -> None:
        if not self.is_empty:
            raise IllegalAction('Finish the current bonus chain first')
        pending = _entry_rewards(rewards)
        if pending:
            self._frames.append(_Frame(pending, ()))

    def available_choices(self) -> tuple[BonusChoice, ...]:
        if self._current is not None or not self._frames:
            return ()
        frame = self._frames[-1]
        return tuple(BonusChoice(reward.source_id, reward.kind, frame.ancestry) for reward in frame.pending)

    def select(self, bonus_id: str) -> BonusChoice:
        choice = next((choice for choice in self.available_choices() if choice.id == bonus_id), None)
        if choice is None:
            raise IllegalAction('That bonus is not available at the current depth')
        frame = self._frames[-1]
        frame.pending = [reward for reward in frame.pending if reward.source_id != bonus_id]
        self._current = choice
        return choice

    def complete_current(self, rewards: Iterable[Reward] = ()) -> None:
        """Finish the selected entry, then expose its children before siblings."""
        if self._current is None:
            raise IllegalAction('Select a bonus first')
        pending = _entry_rewards(rewards)
        ancestry = self._current.ancestry + (self._current.id,)
        occupied = set(ancestry) | {reward.source_id for frame in self._frames for reward in frame.pending}
        if any(reward.source_id in occupied for reward in pending):
            raise IllegalAction('A reward source cannot repeat inside a bonus chain')
        self._current = None
        if pending:
            self._frames.append(_Frame(pending, ancestry))
        while self._frames and not self._frames[-1].pending:
            self._frames.pop()

    def place(self, sheet: PlayerSheet, entry: EntryTarget) -> tuple[Reward, ...]:
        if self._current is None or entry not in bonus_entries(sheet, self._current.kind):
            raise IllegalAction('The entry does not match the selected bonus')
        rewards = apply_entry(sheet, entry)
        self.complete_current(rewards)
        return rewards

    def expire_current(self, sheet: PlayerSheet) -> None:
        if self._current is None or bonus_entries(sheet, self._current.kind):
            raise IllegalAction('Only a bonus with no legal entry can expire')
        self.complete_current()

    def copy(self) -> 'BonusStack':
        """Rewards and choices are frozen values, so only the frames are owned."""
        branch = BonusStack()
        branch._frames = [_Frame(list(frame.pending), frame.ancestry) for frame in self._frames]
        branch._current = self._current
        return branch

    def snapshot(self) -> dict[str, JsonValue]:
        """Controlled persistence only; includes hidden pending siblings."""
        return {
            'version': 1,
            'frames': [{'pending': [{'source_id': reward.source_id, 'kind': reward.kind} for reward in frame.pending],
                        'ancestry': list(frame.ancestry)} for frame in self._frames],
            'current': None if self._current is None else {
                'id': self._current.id, 'kind': self._current.kind, 'ancestry': list(self._current.ancestry)},
        }

    @classmethod
    def restore(cls, snapshot: dict[str, JsonValue]) -> 'BonusStack':
        def fields(value, names):
            if not isinstance(value, dict) or set(value) != set(names):
                raise ValueError('Invalid bonus stack fields')

        def ancestry(value):
            sources = {r.source_id for r in reward_catalog() if r.kind not in ('reroll', 'extra', 'endgame')}
            if not isinstance(value, list) or any(not isinstance(item, str) or item not in sources for item in value):
                raise ValueError('Invalid bonus ancestry')
            if len(value) != len(set(value)):
                raise ValueError('Repeated bonus ancestry')
            return tuple(value)

        fields(snapshot, ('version', 'frames', 'current'))
        if type(snapshot['version']) is not int or snapshot['version'] != 1:
            raise ValueError('Unsupported bonus stack version')
        if not isinstance(snapshot['frames'], list):
            raise ValueError('Invalid bonus frame list')
        stack = cls()
        for frame in snapshot['frames']:
            fields(frame, ('pending', 'ancestry'))
            if not isinstance(frame['pending'], list):
                raise ValueError('Invalid pending bonus list')
            rewards = []
            for item in frame['pending']:
                fields(item, ('source_id', 'kind'))
                rewards.append(Reward(item['source_id'], item['kind']))
            pending = _entry_rewards(rewards)
            if len(pending) != len(rewards):
                raise ValueError('Pending bonus must be an entry reward')
            stack._frames.append(_Frame(pending, ancestry(frame['ancestry'])))
        current = snapshot['current']
        if current is not None:
            fields(current, ('id', 'kind', 'ancestry'))
            reward = Reward(current['id'], current['kind'])
            if not _entry_rewards((reward,)):
                raise ValueError('Current bonus must be an entry reward')
            stack._current = BonusChoice(reward.source_id, reward.kind, ancestry(current['ancestry']))
        if stack._frames:
            if stack._frames[0].ancestry:
                raise ValueError('Bonus chain must start at its root frame')
            for parent, child in zip(stack._frames, stack._frames[1:]):
                if len(child.ancestry) != len(parent.ancestry) + 1 or child.ancestry[:-1] != parent.ancestry:
                    raise ValueError('Bonus frames must follow their ancestor path')
            if stack._current is None and not stack._frames[-1].pending:
                raise ValueError('Empty top bonus frame has no resumable choice')
        if stack._current is not None and (not stack._frames or stack._current.ancestry != stack._frames[-1].ancestry):
            raise ValueError('Current bonus must belong to the top frame')
        occupied = [r.source_id for frame in stack._frames for r in frame.pending]
        if stack._current is not None:
            occupied.append(stack._current.id)
        path = set(stack._frames[-1].ancestry) if stack._frames else set()
        if len(occupied) != len(set(occupied)) or path.intersection(occupied):
            raise ValueError('Repeated pending or ancestor bonus source')
        return stack
