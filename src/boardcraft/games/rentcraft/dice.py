"""Two-die rolls with seeded or scripted results."""

import random
from typing import Protocol


class DiceSource(Protocol):
    def roll(self) -> tuple[int, int]: ...


class RandomDice:
    def __init__(self, rng: random.Random):
        self.rng = rng

    def roll(self) -> tuple[int, int]:
        return self.rng.randint(1, 6), self.rng.randint(1, 6)


class FixedDice:
    def __init__(self, rolls: list[tuple[int, int]]):
        self.rolls = list(rolls)
        self.pos = 0

    def roll(self) -> tuple[int, int]:
        if self.pos >= len(self.rolls):
            raise IndexError("No scripted dice rolls remain.")
        r = self.rolls[self.pos]
        self.pos += 1
        return r
