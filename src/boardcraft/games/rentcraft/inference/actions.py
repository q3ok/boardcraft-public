"""Frozen numbering and legacy mask restrictions for model play."""
from collections.abc import Callable

import numpy as np

from ..bots.trading import counter_for, debtor_sales, payer_purchases, proposals, raise_sales
from ..board import BOARD
from ..actions import Action, ActionKind, Phase
from ..engine import Game
from ..board import BID_STEPS
from ..buildings import unmortgage_cost
from ..memory import sent_this_turn
from ..trade import TRADE_PHASES

PER_SPACE = (ActionKind.BUILD, ActionKind.SELL_BUILDING, ActionKind.MORTGAGE, ActionKind.UNMORTGAGE)
N_PROPOSALS = 4
PASS = Action(ActionKind.PASS)
DECLARE_BANKRUPTCY = Action(ActionKind.DECLARE_BANKRUPTCY)

class StandardActionSpaceV1:

    name = "standard-v1"

    candidates = True

    def __init__(self):
        fixed = [
            Action(ActionKind.ROLL),
            Action(ActionKind.END_TURN),
            Action(ActionKind.BUY),
            Action(ActionKind.DECLINE),
            *(Action(ActionKind.BID, amount=s) for s in BID_STEPS),
            Action(ActionKind.PASS_BID),
            Action(ActionKind.PAY_JAIL_FINE),
            Action(ActionKind.USE_JAIL_CARD),
            Action(ActionKind.ACCEPT_TRADE),
            Action(ActionKind.REJECT_TRADE),
        ]
        per_space = [Action(k, space=i) for k in PER_SPACE for i in range(40)]
        self.actions: list[Action] = fixed + per_space
        self._index = {a: i for i, a in enumerate(self.actions)}
        self.pass_index = self._index[Action(ActionKind.END_TURN)]
        self.bankrupt_index = self._index[Action(ActionKind.DECLINE)]
        self._index[PASS] = self.pass_index
        self._index[DECLARE_BANKRUPTCY] = self.bankrupt_index
        self.fixed_n = len(self.actions)
        self.propose0 = self.fixed_n
        self.counter_index = self.propose0 + N_PROPOSALS
        self.n = self.counter_index + 1

    def proposal_candidates(self, game: Game, player: int) -> list:
        if not game.can_propose():
            return []
        out = []

        s = game.state
        phase = s.phase
        first = debtor_sales(s, player) if phase is Phase.RAISE_FUNDS else (
            payer_purchases(s, player, 0) if phase is Phase.TRADE_WINDOW else [])
        last = []
        if phase is Phase.MORTGAGE_TAKEOVER:
            space = s.pending_takeovers[0][1]
            first = raise_sales(s, player, unmortgage_cost(space), space)
        elif phase is Phase.BUY_DECISION and s.pending_buy is not None:
            last = raise_sales(s, player, BOARD[s.pending_buy].price, s.pending_buy)
        for offer in first + proposals(s, player, reserve=0) + last:
            if game.validate_trade(offer) is None:
                out.append(offer)
            if len(out) == N_PROPOSALS:
                break
        return out

    def counter_candidate(self, game: Game, player: int):
        if not game.can_counter():
            return None
        c = counter_for(game.state, game.state.trade.offer, player)
        return c if c is not None and game.validate_trade(c) is None else None

    def index(self, action: Action) -> int:
        return self._index[action]

    def fixed_action(self, game: Game, i: int) -> Action:

        phase = game.state.phase
        if i == self.pass_index and phase in (Phase.TRADE_WINDOW, Phase.MORTGAGE_TAKEOVER):
            return PASS
        if i == self.bankrupt_index and phase is Phase.RAISE_FUNDS:
            return DECLARE_BANKRUPTCY
        return self.actions[i]

    def to_action(self, game: Game, i: int) -> Action:
        if i < self.fixed_n:
            return self.fixed_action(game, i)
        player = game.decision().player
        if i < self.counter_index:
            return Action(ActionKind.PROPOSE_TRADE, trade=self.proposal_candidates(game, player)[i - self.propose0])
        return Action(ActionKind.COUNTER_TRADE, trade=self.counter_candidate(game, player))

    def mask(self, game: Game) -> np.ndarray:
        m = np.zeros(self.n, dtype=bool)
        for a in game.legal_actions():
            m[self._index[a]] = True
        d = game.decision()
        if d is not None:
            if d.phase in TRADE_PHASES:
                for k, offer in enumerate(self.proposal_candidates(game, d.player) if self.candidates else ()):
                    m[self.propose0 + k] = not sent_this_turn(game.state, offer)
                if d.phase is not Phase.RAISE_FUNDS:
                    self._block_reversals(game, m)
            elif d.phase is Phase.TRADE_RESPONSE:
                if self.candidates:
                    counter = self.counter_candidate(game, d.player)
                    m[self.counter_index] = counter is not None and not sent_this_turn(game.state, counter)
                self._block_reversals(game, m)
        return m

    def _block_reversals(self, game: Game, m: np.ndarray) -> None:

        mem = game.state.memory
        s = game.state
        if mem is None or mem.changed_turn != s.turn:
            return
        for sp, sign in mem.changed.items():
            for kind in (ActionKind.UNMORTGAGE, ActionKind.BUILD) if sign < 0 else (ActionKind.MORTGAGE, ActionKind.SELL_BUILDING):
                m[self._index[Action(kind, space=sp)]] = False
        if s.phase is Phase.MORTGAGE_TAKEOVER and s.pending_takeovers:

            payoff = Action(ActionKind.UNMORTGAGE, space=s.pending_takeovers[0][1])
            m[self._index[payoff]] = payoff in game.legal_actions()

