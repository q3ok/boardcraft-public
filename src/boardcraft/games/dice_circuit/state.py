"""Mutable player-sheet state owned by a game engine."""
from dataclasses import dataclass, field

from .sheet import load_sheet


@dataclass
class PlayerSheet:
    yellow: set[tuple[int, int]] = field(default_factory=lambda: set(load_sheet().yellow.premarked))
    blue: set[int] = field(default_factory=set)
    green: int = 0
    orange: list[int] = field(default_factory=list)
    purple: list[int] = field(default_factory=list)
    earned_rewards: set[str] = field(default_factory=set)
    rerolls: int = 0
    extras: int = 0
    emblems: int = 0
    rerolls_used: int = 0
    extras_used: int = 0

    def copy(self) -> 'PlayerSheet':
        return PlayerSheet(set(self.yellow), set(self.blue), self.green, list(self.orange), list(self.purple),
                           set(self.earned_rewards), self.rerolls, self.extras, self.emblems,
                           self.rerolls_used, self.extras_used)

    @property
    def rerolls_earned(self) -> int:
        return self.rerolls + self.rerolls_used

    @property
    def extras_earned(self) -> int:
        return self.extras + self.extras_used
