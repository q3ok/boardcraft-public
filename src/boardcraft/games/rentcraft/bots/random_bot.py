"""Choose uniformly among the enumerated legal moves."""
import random

from ..actions import Decision
from ..state import GameState
from . import Choice


class RandomBot:
    label = 'random'

    def __init__(self, seed: int):
        self.rng = random.Random(seed)

    def choose(self, state: GameState, decision: Decision) -> Choice:
        return Choice(self.rng.choice(decision.actions), 'Random choice')
