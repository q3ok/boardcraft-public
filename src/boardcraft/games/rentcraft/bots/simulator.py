"""Compare candidate actions on paired hypothetical games played by Calculator."""
import copy
import dataclasses
import random

from ..actions import Action, ActionKind, Decision, IllegalActionError, Phase
from ..engine import Game
from ..state import GameState
from ..debt import net_worth
from ..dice import RandomDice
from ..observation import sample_decks
from ..text import seat_label
from . import Choice, describe_trade
from .calculator import CalculatorBot
from .profiles import PROFILES

SAMPLES = 12
HORIZON = 25
MAX_PROPOSALS = 3

ROLL = Action(ActionKind.ROLL)
END_TURN = Action(ActionKind.END_TURN)


def position_value(state: GameState, me: int) -> float:
    if state.players[me].bankrupt:
        return 0.0
    alive = [i for i, p in enumerate(state.players) if not p.bankrupt]
    if len(alive) == 1:
        return 1.0
    worths = {i: max(net_worth(state, i), 0) for i in alive}
    total = sum(worths.values())
    return worths[me] / total if total else 1.0 / len(alive)


def _rollout_bot() -> CalculatorBot:
    bot = CalculatorBot()
    bot.profile = _ROLLOUT_PROFILE
    return bot


_ROLLOUT_PROFILE = dataclasses.replace(PROFILES['calculator'], trade_variation=0.0, random_move_probability=0.0)


def rollout(state: GameState, me: int, action: Action, seed: int, horizon: int = HORIZON) -> float | None:
    """Score one option with common dice and sample only unrevealed card positions."""
    rng = random.Random(seed)
    s = copy.deepcopy(state)
    s.decks = sample_decks(s.players, s.memory.known_bottom, rng)

    s.config = dataclasses.replace(s.config, trade_windows="off")
    g = Game(s, rng, RandomDice(rng))
    try:
        g.apply(action)
    except IllegalActionError:
        return None
    bots = [_rollout_bot() for _ in s.players]
    end = s.round + horizon
    steps = 0
    while not g.is_over and g.state.round < end and steps < 5000:
        d = g.decision()
        if d is None:
            break
        steps += 1
        try:
            g.apply(bots[d.player].choose(g.state, d).action)
        except IllegalActionError:
            g.apply(d.actions[0])
    return position_value(g.state, me)


class SimulatorBot(CalculatorBot):
    def __init__(self, seed: int = 0, samples: int = SAMPLES, horizon: int = HORIZON):
        super().__init__()
        self.profile = PROFILES['simulator']
        self.label = 'simulator'
        self.samples, self.horizon = samples, horizon
        self._rng = random.Random(seed)
        self._last_why = ''
        self._proposed_turn = -1

    def _best(self, state: GameState, me: int, options: list[tuple[Action, str]]) -> tuple[Action, str]:
        seeds = [self._rng.randrange(1 << 30) for _ in range(self.samples)]
        scored = []
        for action, label in options:
            vals = [v for v in (rollout(state, me, action, sd, self.horizon) for sd in seeds) if v is not None]
            if vals:
                scored.append((sum(vals) / len(vals), action, label))
        if not scored:
            return options[0][0], 'No rollout produced a score'
        _, action, label = max(scored, key=lambda row: row[0])
        return action, f"{label}. This choice had the best mean position in the simulated games."

    def _decide(self, state: GameState, decision: Decision) -> Choice:
        me = decision.player
        acts = decision.actions
        if decision.phase is Phase.BUY_DECISION and Action(ActionKind.BUY) in acts:
            a, why = self._best(state, me, [(Action(ActionKind.BUY), 'Buy'), (Action(ActionKind.DECLINE), 'Decline')])
            return Choice(a, why)
        if decision.phase is Phase.TRADE_RESPONSE:
            opts = [(Action(ActionKind.ACCEPT_TRADE), 'Accept'), (Action(ActionKind.REJECT_TRADE), 'Reject')]
            base = super()._decide(state, decision)
            if base.action.kind is ActionKind.COUNTER_TRADE:
                opts.append((base.action, 'counteroffer'))
            a, why = self._best(state, me, opts)
            return Choice(a, why)
        if decision.phase is Phase.PRE_ROLL and state.players[me].in_jail:
            opts = [(a, label) for a, label in ((Action(ActionKind.USE_JAIL_CARD), 'jail release card'), (Action(ActionKind.PAY_JAIL_FINE), 'bail'))
                    if a in acts] + [(ROLL, 'Stay and roll')]
            if len(opts) > 1:
                a, why = self._best(state, me, opts)
                return Choice(a, why)
        return super()._decide(state, decision)

    def _proposal(self, state: GameState, pid: int):
        if self._proposed_turn == state.turn or state.trades_this_turn >= 1:
            return None
        self._proposed_turn = state.turn
        cands = [o for o in self._candidates(state, pid)][:MAX_PROPOSALS]
        if not cands:
            return None
        names = [seat_label(i) for i in range(len(state.players))]
        opts = [(Action(ActionKind.PROPOSE_TRADE, trade=o), describe_trade(o, names)) for o in cands]
        opts.append((ROLL if state.phase is Phase.PRE_ROLL else END_TURN, 'No trade'))
        best, why = self._best(state, pid, opts)
        self._last_why = why
        return best.trade if best.kind is ActionKind.PROPOSE_TRADE else None

    def _proposal_reason(self, state: GameState, offer, names: list[str]) -> str:
        return getattr(self, "_last_why", "") or super()._proposal_reason(state, offer, names)

    def _window_reason(self, state: GameState, offer, names: list[str]) -> str:
        return f'Trade window without rollouts: {describe_trade(offer, names)}'
