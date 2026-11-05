"""Compare paired rollouts in batches and eliminate clearly inferior moves."""
import math

from ..actions import Action, ActionKind, Decision, Phase
from ..board import BOARD
from ..state import GameState
from . import Choice
from .profiles import PROFILES
from .simulator import SimulatorBot, rollout
from .trading import trade_score

BATCH = 4
CLEAR_TRADE = 300
CLEAR_BUY_CASH = 600


class FastSimulatorBot(SimulatorBot):
    def __init__(self, seed: int = 0, samples: int = 12, horizon: int = 25):
        super().__init__(seed, samples, horizon)
        self.profile = PROFILES['fast-simulator']
        self.label = 'fast-simulator'

    def _best(self, state: GameState, me: int, options: list[tuple[Action, str]]) -> tuple[Action, str]:
        alive = list(range(len(options)))
        vals: dict[int, list[float | None]] = {i: [] for i in alive}
        used = 0
        while used < self.samples and len(alive) > 1:
            seeds = [self._rng.randrange(1 << 30) for _ in range(min(BATCH, self.samples - used))]
            used += len(seeds)
            for i in alive:
                vals[i] += [rollout(state, me, options[i][0], sd, self.horizon) for sd in seeds]
            alive = [i for i in alive if any(v is not None for v in vals[i])]
            if not alive:
                return options[0][0], 'No rollout produced a score'
            mean = {i: _mean(vals[i]) for i in alive}
            lead = max(alive, key=lambda i: mean[i])
            alive = [i for i in alive if i == lead or not _clearly_worse(vals[i], vals[lead])]
        best = max(alive, key=lambda i: _mean(vals[i]))
        action, label = options[best]
        return action, f"{label}. This choice led after comparing batches of simulated games."

    def _decide(self, state: GameState, decision: Decision) -> Choice:
        me = decision.player
        if decision.phase is Phase.TRADE_RESPONSE:
            score = trade_score(state, state.trade.offer, me)
            if abs(score) > CLEAR_TRADE:
                base = super(SimulatorBot, self)._decide(state, decision)
                return Choice(base.action, f'Clear choice without rollouts (offer score {score:+.0f}): {base.explanation}')
        if decision.phase is Phase.BUY_DECISION and Action(ActionKind.BUY) in decision.actions:
            left = state.players[me].cash - BOARD[state.pending_buy].price
            if left >= CLEAR_BUY_CASH:
                return Choice(Action(ActionKind.BUY), f'Buy without a rollout, leaving ${left}')
        return super()._decide(state, decision)


def _mean(xs) -> float:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else -1.0


def _clearly_worse(a: list, b: list) -> bool:
    d = [x - y for x, y in zip(a, b) if x is not None and y is not None]
    if len(d) < BATCH:
        return False
    m = sum(d) / len(d)
    sd = math.sqrt(sum((x - m) ** 2 for x in d) / (len(d) - 1)) if len(d) > 1 else 0.0
    return m + 2 * sd / math.sqrt(len(d)) < 0
