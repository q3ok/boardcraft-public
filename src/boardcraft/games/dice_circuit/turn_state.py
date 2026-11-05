"""Explicit mutable turn state and detached sheet/chain projections."""
from dataclasses import dataclass, field
from boardcraft.contracts import JsonValue
from .bonuses import BonusStack
from .state import PlayerSheet


@dataclass
class PlayerWindow:
    stage: str = 'done'
    extra_used: set[str] = field(default_factory=set)
    stack: BonusStack = field(default_factory=BonusStack)

    def copy(self) -> 'PlayerWindow':
        return PlayerWindow(self.stage, set(self.extra_used), self.stack.copy())


@dataclass
class TurnState:
    starting: int
    active: int
    sheets: list[PlayerSheet]
    windows: list[PlayerWindow]
    round_no: int = 1
    turn_index: int = 0
    phase: str = 'setup'
    dice: dict[str, int] = field(default_factory=dict)
    available: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    tray: list[str] = field(default_factory=list)
    regular_roll: int = 0
    reroll_no: int = 0
    frozen_sheets: list[dict] | None = None
    resigned: int | None = None  # the seat that surrendered and ended the game

    def copy(self) -> 'TurnState':
        """Frozen sheets are replaced as a whole and never edited, so copies may share them."""
        return TurnState(self.starting, self.active, [sheet.copy() for sheet in self.sheets],
                         [window.copy() for window in self.windows], self.round_no, self.turn_index, self.phase,
                         dict(self.dice), list(self.available), list(self.kept), list(self.tray),
                         self.regular_roll, self.reroll_no, self.frozen_sheets, self.resigned)


def sheet_view(sheet: PlayerSheet) -> dict[str, JsonValue]:
    return {'yellow': [list(pair) for pair in sorted(sheet.yellow)], 'blue': sorted(sheet.blue),
            'green': sheet.green, 'orange': list(sheet.orange), 'purple': list(sheet.purple),
            'earned_rewards': sorted(sheet.earned_rewards), 'rerolls': sheet.rerolls,
            'extras': sheet.extras, 'emblem_count': sheet.emblems,
            'rerolls_earned': sheet.rerolls_earned, 'rerolls_used': sheet.rerolls_used,
            'extras_earned': sheet.extras_earned, 'extras_used': sheet.extras_used}


def bonus_view(stack: BonusStack) -> dict[str, JsonValue]:
    """Owner's planning context includes locked siblings and complete ancestry.

    Explicitly project only rule context, rather than returning a persistence
    object. There are no random keys, counters or other players' stacks here.
    """
    saved = stack.snapshot()
    return {'frames': [{'pending': [{'source_id': item['source_id'], 'kind': item['kind']}
                                   for item in frame['pending']], 'ancestry': list(frame['ancestry'])}
                       for frame in saved['frames']],
            'current': None if stack.current is None else {'id': stack.current.id, 'kind': stack.current.kind,
                                                         'ancestry': list(stack.current.ancestry)}}
