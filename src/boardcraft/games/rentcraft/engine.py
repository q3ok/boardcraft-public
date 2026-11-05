"""Rentcraft turn resolution and resumable decisions."""

import copy
import random
from dataclasses import dataclass

from boardcraft import contracts
from . import observation, persistence
from .text import describe_action

from .actions import Action, ActionKind, Decision, Event, IllegalActionError, Phase
from .board import (
    BOARD,
    BUYABLE,
    GO_SALARY,
    GO_TO_JAIL_INDEX,
    HOTEL,
    JAIL_FINE,
    JAIL_INDEX,
    MAX_JAIL_TURNS,
    RAILROADS,
    TOTAL_HOTELS,
    TOTAL_HOUSES,
    UTILITIES,
    Kind,
)
from .buildings import (
    owned_spaces,
    apply_property_action,
    unmortgage_cost,
    build_actions,
    mortgage_actions,
    sell_actions,
    unmortgage_actions,
)
from .cards import JAIL_FREE_ID, Card, draw_card
from .auction import apply_auction, auction_actions, current_bidder, remove_bidder, start_auction
from .debt import GameResult, charge, debt_creditors, declare_bankruptcy, interest, net_worth, pay_debt, split_bankruptcy
from .dice import DiceSource, RandomDice
from .memory import Memory, ModelHistory, act_context, held_jail_cards, note_turn, remember
from .trade import (
    TRADE_PHASES,
    TradeOffer,
    TradeState,
    can_propose,
    execute_offer,
    proposal_possible,
    trade_limit,
    trade_proposer,
    trades_used,
    validate_offer,
)
from .rent import rent_for
from .liquidity import bank_funds, liquidation_step
from .state import Debt, GameConfig, GameState, PlayerState, TradeWindow

def _nearest(position: int, targets: tuple[int, ...]) -> int:
    ahead = [t for t in targets if t > position]
    return ahead[0] if ahead else targets[0]


ROLL = Action(ActionKind.ROLL)
END_TURN = Action(ActionKind.END_TURN)
BUY = Action(ActionKind.BUY)
DECLINE = Action(ActionKind.DECLINE)
PAY_JAIL_FINE = Action(ActionKind.PAY_JAIL_FINE)
USE_JAIL_CARD = Action(ActionKind.USE_JAIL_CARD)
ACCEPT_TRADE = Action(ActionKind.ACCEPT_TRADE)
REJECT_TRADE = Action(ActionKind.REJECT_TRADE)
PASS = Action(ActionKind.PASS)
DECLARE_BANKRUPTCY = Action(ActionKind.DECLARE_BANKRUPTCY)
PROPERTY_ACTIONS = (ActionKind.BUILD, ActionKind.SELL_BUILDING, ActionKind.MORTGAGE, ActionKind.UNMORTGAGE)
_UNMORTGAGE = tuple(Action(ActionKind.UNMORTGAGE, space=i) for i in range(len(BOARD)))


class Game:
    """Mutable rules position for trusted local play and independent simulations."""

    def __init__(self, state: GameState, rng: random.Random, dice: DiceSource):
        self.state = state
        self.rng = rng
        self.dice = dice
        self.last_events: list[Event] = []
        self._deferring = False

    @classmethod
    def new(cls, config: GameConfig, dice: DiceSource | None = None) -> "Game":
        rng = random.Random(config.seed)
        decks = {"opportunity": list(range(16)), "community_fund": list(range(16))}
        rng.shuffle(decks["opportunity"])
        rng.shuffle(decks["community_fund"])
        state = GameState(
            config=config,
            players=[PlayerState(id=i, cash=config.start_cash) for i in range(config.num_players)],
            owner=[None] * len(BOARD),
            buildings=[0] * len(BOARD),
            mortgaged=[False] * len(BOARD),
            bank_houses=TOTAL_HOUSES,
            bank_hotels=TOTAL_HOTELS,
            decks=decks,
            memory=Memory.new(config.num_players),
        )
        game = cls(state, rng, dice if dice is not None else RandomDice(rng))
        game._start_roll()
        state.memory.model_history = ModelHistory(complete_from_start=True, last_current=state.current)
        return game

    def _start_roll(self) -> None:
        s = self.state
        contenders = list(range(len(s.players)))
        tiebreak = 1
        while True:
            totals = {}
            for pid in contenders:
                d1, d2 = self.dice.roll()
                totals[pid] = d1 + d2
                s.start_rolls.append((pid, (d1, d2), tiebreak))
                self._emit("start_roll", pid, dice=(d1, d2), tiebreak=tiebreak)
            best = max(totals.values())
            contenders = [pid for pid in contenders if totals[pid] == best]
            if len(contenders) == 1:
                break
            tiebreak += 1
        s.current = s.first_player = contenders[0]
        self._emit("start_player", s.current, total=best)


    def clone(self) -> "Game":
        return copy.deepcopy(self)

    @property
    def is_over(self) -> bool:
        return self.state.phase is Phase.GAME_OVER

    def decision(self) -> Decision | None:
        if self.is_over:
            return None
        return Decision(self._decider(), self.state.phase, self.legal_actions())

    def legal_actions(self) -> tuple[Action, ...]:
        s = self.state
        if s.phase is Phase.POST_ROLL:
            return tuple(self._property_actions() + self._base_actions())
        if s.phase is Phase.RAISE_FUNDS:
            liquidation = self._liquidation(s.debts[0].debtor)
            return tuple(liquidation) if liquidation else (DECLARE_BANKRUPTCY,)
        if s.phase is Phase.MORTGAGE_TAKEOVER:
            return tuple(self._base_actions() + self._property_actions())
        return tuple(self._base_actions() + self._property_actions())

    def _base_actions(self) -> list[Action]:
        s = self.state
        p = s.players[s.current]
        if s.phase is Phase.PRE_ROLL:
            acts = [ROLL]
            if p.in_jail:
                if p.cash >= JAIL_FINE:
                    acts.append(PAY_JAIL_FINE)
                if p.jail_cards:
                    acts.append(USE_JAIL_CARD)
            return acts
        if s.phase is Phase.POST_ROLL:
            return [ROLL if s.extra_roll and not p.in_jail else END_TURN]
        if s.phase is Phase.BUY_DECISION:
            return [BUY, DECLINE] if p.cash >= BOARD[s.pending_buy].price else [DECLINE]
        if s.phase is Phase.AUCTION:
            return list(auction_actions(s))
        if s.phase is Phase.TRADE_RESPONSE:
            if validate_offer(s, s.trade.offer) is not None:
                return [REJECT_TRADE]
            return [ACCEPT_TRADE, REJECT_TRADE]
        if s.phase is Phase.TRADE_WINDOW:
            return [PASS]
        if s.phase is Phase.MORTGAGE_TAKEOVER:
            return [PASS]
        if s.phase is Phase.RAISE_FUNDS and not self._liquidation(s.debts[0].debtor):
            return [DECLARE_BANKRUPTCY]
        return []

    def _property_actions(self) -> list[Action]:
        s = self.state
        if s.phase in (Phase.PRE_ROLL, Phase.POST_ROLL, Phase.BUY_DECISION):
            return self._management(s.current)
        if s.phase is Phase.RAISE_FUNDS:
            return self._liquidation(s.debts[0].debtor)
        if s.phase is Phase.AUCTION:
            return self._management(current_bidder(s))
        if s.phase is Phase.TRADE_RESPONSE:
            return self._management(s.trade.offer.target)
        if s.phase is Phase.MORTGAGE_TAKEOVER:
            pid, space = s.pending_takeovers[0]
            payoff = [_UNMORTGAGE[space]] if s.players[pid].cash >= unmortgage_cost(space) else []
            waiting = {sp for p, sp in s.pending_takeovers if p == pid}
            return payoff + [a for a in self._management(pid)
                             if not (a.kind is ActionKind.UNMORTGAGE and a.space in waiting)]
        if s.phase is Phase.TRADE_WINDOW:
            pid = s.window.queue[0]
            return self._management(pid)
        return []


    def can_propose(self) -> bool:
        return can_propose(self.state)

    def can_counter(self) -> bool:
        s = self.state
        return s.phase is Phase.TRADE_RESPONSE and s.trade.exchanges < s.config.max_trade_exchanges

    def validate_trade(self, offer: TradeOffer) -> str | None:
        return validate_offer(self.state, offer)

    def _check_trade_action(self, action: Action) -> None:
        s = self.state
        offer = action.trade
        if not isinstance(offer, TradeOffer):
            raise IllegalActionError("Missing trade offer.")
        if action.kind is ActionKind.PROPOSE_TRADE:
            proposer = trade_proposer(s)
            if proposer is None:
                raise IllegalActionError(f"Trading is unavailable during {s.phase.value}.")
            if offer.proposer != proposer:
                raise IllegalActionError("Only the player making the decision can propose a trade.")
            if s.config.max_trade_exchanges == 0 or trades_used(s) >= trade_limit(s):
                what = s.phase.value
                raise IllegalActionError(
                    f"The proposal limit for {what} has been reached.",
                    "LIMIT_REACHED", limit=s.config.max_trades_per_turn, scope=what,
                )
        else:
            if s.phase is not Phase.TRADE_RESPONSE:
                raise IllegalActionError("There is no outstanding offer to counter.")
            pending = s.trade.offer
            if offer.proposer != pending.target or offer.target != pending.proposer:
                raise IllegalActionError("Address the counteroffer to the preceding proposer.")
            if not self.can_counter():
                raise IllegalActionError(
                    "The offer limit for this negotiation has been reached.",
                    "LIMIT_REACHED", limit=s.config.max_trade_exchanges, scope="negotiation",
                )
        problem = validate_offer(s, offer, raisable=True)
        if problem:
            raise IllegalActionError(problem)

    def _trade(self, action: Action) -> None:
        s = self.state
        kind = action.kind
        if kind is ActionKind.PROPOSE_TRADE:
            if s.phase is Phase.RAISE_FUNDS:
                s.debts[0].trades += 1
            elif s.phase is Phase.AUCTION:
                s.auction.trades[action.trade.proposer] = s.auction.trades.get(action.trade.proposer, 0) + 1
            elif s.phase is Phase.MORTGAGE_TAKEOVER:
                s.takeover_trades += 1
            elif s.phase is not Phase.TRADE_WINDOW:
                s.trades_this_turn += 1
            s.trade = TradeState(
                offer=action.trade, resume_phase=s.phase, history=[action.trade], outer_resume=s.resume_phase,
                in_window=s.window is not None,
            )
            s.resume_phase = None
            s.phase = Phase.TRADE_RESPONSE
            self._emit("trade_offer", action.trade.proposer, offer=action.trade)
        elif kind is ActionKind.COUNTER_TRADE:
            s.trade.offer = action.trade
            s.trade.history.append(action.trade)
            s.trade.exchanges += 1
            self._emit("trade_counter", action.trade.proposer, offer=action.trade)
        elif kind is ActionKind.REJECT_TRADE:
            offer = s.trade.offer
            trade = s.trade
            self._emit("trade_reject", offer.target, offer=offer)
            self._close_trade()
            self._after_negotiation(trade)
        else:
            offer = s.trade.offer
            problem = validate_offer(s, offer)
            if problem:
                raise IllegalActionError(f"The offer has expired. {problem}", "OFFER_STALE",
                                         reason=getattr(problem, "code", None), **getattr(problem, "fields", {}))
            trade = s.trade
            self._close_trade()
            if s.phase not in (Phase.RAISE_FUNDS, Phase.AUCTION):
                s.resume_phase = trade.resume_phase
            self.last_events.extend(execute_offer(s, offer))
            if s.phase is Phase.RAISE_FUNDS:
                self._settle_debts()
            else:
                s.resume_phase = trade.outer_resume
            self._after_negotiation(trade)

    def _close_trade(self) -> None:
        """Restore both return points and consume the initiating seat's window allowance."""
        s = self.state
        t = s.trade
        s.phase = t.resume_phase
        s.resume_phase = t.outer_resume
        s.trade = None
        w = s.window
        if t.resume_phase is Phase.TRADE_WINDOW and w is not None and w.queue and w.queue[0] == t.history[0].proposer:
            w.queue.pop(0)

    def _after_negotiation(self, trade: TradeState) -> None:
        s = self.state
        if self.is_over:
            return
        if s.phase is Phase.TRADE_WINDOW:
            self._window_step()
            return
        if s.phase is Phase.RAISE_FUNDS:
            self._settle_debts()
            return
        if trade.resume_phase is Phase.MORTGAGE_TAKEOVER:
            return
        if not trade.in_window and trade.resume_phase is not Phase.TRADE_WINDOW and s.trade is None:
            first = trade.history[0]
            self._open_window("after_trade", first.proposer, "restore", exclude=(first.proposer, first.target))

    def apply(self, action: Action) -> list[Event]:
        s = self.state
        turn, actor, phase = s.turn, self._decider(), s.phase
        context = act_context(s)
        takeover = s.pending_takeovers[0][1] if phase is Phase.MORTGAGE_TAKEOVER and s.pending_takeovers else None
        held = held_jail_cards(s)
        events = self._apply(action)
        self._post()
        remember(s, events, held)
        note_turn(s, turn, actor, phase, action, events, takeover, context)
        return events

    def can_request_trade(self, player: int) -> bool:
        s = self.state
        if self.is_over or s.players[player].bankrupt:
            return False
        if player in s.trade_requests:
            return False
        if s.window and player in s.window.queue:
            return player not in s.window.requested
        if player == self._decider() and self.can_propose():
            return False
        return s.requests_this_turn.get(player, 0) < s.config.max_trades_per_turn

    def request_trade(self, player: int) -> list[Event]:
        s = self.state
        if self.is_over:
            raise IllegalActionError("The game is over.")
        if not 0 <= player < len(s.players) or s.players[player].bankrupt:
            raise IllegalActionError(f"Seat {player} cannot request a trade.")
        if not self.can_request_trade(player):
            raise IllegalActionError("A new trade request is unavailable for this seat.")
        self.last_events = []
        self._emit("trade_request", player)
        w = s.window
        if w is not None and player in w.queue:
            w.requested.add(player)
        else:
            s.requests_this_turn[player] = s.requests_this_turn.get(player, 0) + 1
            s.trade_requests.append(player)
        self._post()
        remember(s, self.last_events)
        return self.last_events

    def _post(self) -> None:
        """Finish title takeovers before servicing requests that waited during a decision."""
        s = self.state
        negotiating = s.trade is not None and s.trade.resume_phase is Phase.MORTGAGE_TAKEOVER
        if (s.pending_takeovers or s.takeover_owed) and not negotiating:
            self._takeover_step()
        s.trade_requests = [p for p in s.trade_requests if not s.players[p].bankrupt]
        if s.phase is Phase.MORTGAGE_TAKEOVER:
            return
        if s.trade_requests and s.window is None and s.trade is None and not self.is_over:
            queue, s.trade_requests = list(s.trade_requests), []
            self._open_window("request", None, "restore", queue=queue)


    def _takeover_step(self) -> None:
        s = self.state
        if self.is_over:
            s.pending_takeovers, s.takeover_owed = [], []
            return
        while s.pending_takeovers:
            pid, space = s.pending_takeovers[0]
            if s.players[pid].bankrupt or s.owner[space] != pid or not s.mortgaged[space]:
                s.pending_takeovers.pop(0)
                s.takeover_trades = 0
                continue
            if s.phase is not Phase.MORTGAGE_TAKEOVER:
                s.takeover_return = (s.phase, s.resume_phase)
                s.phase, s.resume_phase = Phase.MORTGAGE_TAKEOVER, None
            return
        self._takeover_end()

    def _takeover_choice(self, payoff: bool) -> None:
        s = self.state
        pid, space = s.pending_takeovers.pop(0)
        s.takeover_trades = 0
        if payoff:
            cost = unmortgage_cost(space)
            s.players[pid].cash -= cost
            s.mortgaged[space] = False
            self._emit("unmortgage", pid, space=space, amount=cost, takeover=True)
        else:
            self._takeover_interest(pid, space)
        self._takeover_step()

    def _takeover_interest(self, pid: int, space: int) -> None:
        s = self.state
        amount = interest(space)
        if s.players[pid].cash >= amount:
            s.players[pid].cash -= amount
            self._emit("pay", pid, to=None, amount=amount, reason="interest", space=space)
        else:
            s.takeover_owed.append((pid, space, amount))

    def _takeover_end(self, settle: bool = True) -> None:
        """Resume the parent decision, then collect any interest the new owners deferred."""
        s = self.state
        if s.phase is Phase.MORTGAGE_TAKEOVER:
            s.phase, s.resume_phase = s.takeover_return
        s.takeover_return = None
        s.takeover_trades = 0
        owed, s.takeover_owed = [x for x in s.takeover_owed if not s.players[x[0]].bankrupt], []
        if not owed or self.is_over:
            if settle and s.phase is Phase.RAISE_FUNDS and not self.is_over:
                self._settle_debts()
            return
        outer = s.resume_phase
        if s.phase not in (Phase.RAISE_FUNDS, Phase.AUCTION):
            s.resume_phase = s.phase
        for pid, space, amount in owed:
            self._charge(pid, None, amount, reason="interest", space=space)
        if s.phase is Phase.RAISE_FUNDS:
            self._settle_debts()
        else:
            s.resume_phase = outer

    def _takeover_flush(self) -> None:
        s = self.state
        while s.pending_takeovers:
            pid, space = s.pending_takeovers.pop(0)
            if not s.players[pid].bankrupt and s.owner[space] == pid and s.mortgaged[space]:
                self._takeover_interest(pid, space)
        self._takeover_end(settle=False)


    def _windows_allow(self, kind: str) -> bool:
        level = self.state.config.trade_windows
        return kind == "request" or level == "full" or (level == "payments" and kind == "payment")

    def _open_window(self, kind: str, trigger: int | None, then: str, exclude=(), queue=None, payments=None,
                     space: int | None = None) -> None:
        s = self.state
        calm = s.window is None and s.trade is None and not self.is_over and (
            kind == "request" or (not s.debts and s.auction is None)
        )
        if not (calm and self._windows_allow(kind)):
            self._run_then(then, payments or [])
            return
        if queue is None:
            n = len(s.players)
            order = [(trigger + k) % n for k in range(1, n)]
            queue = [p for p in order if not s.players[p].bankrupt and p not in exclude]
        s.windows_opened += 1
        s.window = TradeWindow(kind, trigger, list(queue), then, s.phase, s.resume_phase, s.windows_opened,
                               list(payments or []), space, set(queue) if kind == "request" else set(),
                               in_auction=s.auction is not None)
        s.resume_phase = None
        s.phase = Phase.TRADE_WINDOW
        amount = sum(p[2] for p in s.window.payments)
        self._emit("trade_window", trigger, window=kind, space=space, amount=amount, queue=list(queue))
        self._window_step()

    def _window_step(self) -> None:
        s = self.state
        w = s.window
        if w is None or s.phase is not Phase.TRADE_WINDOW:
            return
        while w.queue and not proposal_possible(s, w.queue[0]):
            w.queue.pop(0)
        if w.queue:
            p = w.queue[0]
            if p in s.trade_requests:
                s.trade_requests.remove(p)
                w.requested.add(p)
            return
        self._close_window()

    def _close_window(self) -> None:
        s = self.state
        w = s.window
        s.window = None
        s.phase, s.resume_phase = w.phase, w.resume_phase
        self._emit("trade_window_end", w.trigger, window=w.kind)
        self._run_then(w.then, w.payments)

    def _run_then(self, then: str, payments: list) -> None:
        s = self.state
        if self.is_over:
            return
        if then == "end_turn":
            self._end_turn()
            return
        if then == "continue":
            self._continue_turn()
            return
        if then == "payments":
            for debtor, creditor, amount, info in payments:
                if not s.players[debtor].bankrupt:
                    self._charge(debtor, creditor, amount, **info)
            if s.players[s.current].bankrupt and s.phase is not Phase.RAISE_FUNDS:
                self._continue_turn()
            elif s.phase is Phase.RAISE_FUNDS:
                self._settle_debts()
            elif s.pending_move is not None:
                self._continue_turn()
            else:
                self._after_resolution()
            return
        if s.phase is Phase.AUCTION:
            if s.auction is None:
                self._continue_turn()
        elif s.phase is Phase.RAISE_FUNDS:
            self._settle_debts()
        elif s.players[s.current].bankrupt and s.auction is None and s.phase is not Phase.TRADE_RESPONSE:
            self._continue_turn()

    def _apply(self, action: Action) -> list[Event]:
        # Validate the complete action before replacing the previous receipt or mutating state.
        if action.kind in (ActionKind.PROPOSE_TRADE, ActionKind.COUNTER_TRADE):
            self._check_trade_action(action)
            self.last_events = []
            self._trade(action)
            return self.last_events
        allowed = self._property_actions() if action.kind in PROPERTY_ACTIONS else self._base_actions()
        if action not in allowed:
            raise IllegalActionError(
                f"The action {action.kind.value} is unavailable during {self.state.phase.value}."
            )
        self.last_events = []
        kind = action.kind
        if kind is ActionKind.ROLL:
            self._roll()
        elif kind is ActionKind.END_TURN:
            self._open_window("turn_end", self.state.current, "end_turn")
        elif kind is ActionKind.PASS and self.state.phase is Phase.MORTGAGE_TAKEOVER:
            self._takeover_choice(payoff=False)
        elif kind is ActionKind.PASS:
            self.state.window.queue.pop(0)
            self._window_step()
        elif kind is ActionKind.DECLARE_BANKRUPTCY:
            self._bankrupt_debtor(self.state.debts[0])
            if not self._check_last_player():
                self._settle_debts()
        elif kind is ActionKind.BUY:
            self._buy()
        elif kind is ActionKind.DECLINE:
            self._decline()
        elif kind in (ActionKind.BID, ActionKind.PASS_BID):
            self._auction(action)
        elif kind is ActionKind.PAY_JAIL_FINE:
            self._pay_jail_fine()
        elif kind is ActionKind.USE_JAIL_CARD:
            self._use_jail_card()
        elif kind in (ActionKind.ACCEPT_TRADE, ActionKind.REJECT_TRADE):
            self._trade(action)
        elif (kind is ActionKind.UNMORTGAGE and self.state.phase is Phase.MORTGAGE_TAKEOVER
              and action.space == self.state.pending_takeovers[0][1]):
            self._takeover_choice(payoff=True)
        elif kind in PROPERTY_ACTIONS:
            s = self.state
            if s.phase is Phase.TRADE_WINDOW:
                s.window.requested.add(s.window.queue[0])
            self.last_events.extend(apply_property_action(s, action))
            if s.phase is Phase.RAISE_FUNDS:
                self._settle_debts()
        return self.last_events


    def _management(self, pid: int) -> list[Action]:
        s = self.state
        owned = owned_spaces(s, pid)
        if not owned:
            return []
        return (
            build_actions(s, pid, owned)
            + sell_actions(s, pid, owned)
            + mortgage_actions(s, pid, owned)
            + unmortgage_actions(s, pid, owned)
        )

    def _decider(self) -> int:
        s = self.state
        if s.phase is Phase.AUCTION:
            return current_bidder(s)
        if s.phase is Phase.RAISE_FUNDS:
            return s.debts[0].debtor
        if s.phase is Phase.TRADE_RESPONSE:
            return s.trade.offer.target
        if s.phase is Phase.TRADE_WINDOW:
            return s.window.queue[0]
        if s.phase is Phase.MORTGAGE_TAKEOVER:
            return s.pending_takeovers[0][0]
        return s.current

    def _emit(self, kind: str, player: int | None, **data) -> None:
        self.last_events.append(Event(kind, player, data))

    def _roll(self) -> None:
        s = self.state
        pid = s.current
        d1, d2 = self.dice.roll()
        s.last_roll = (d1, d2)
        s.roll_count += 1
        self._emit("roll", pid, dice=(d1, d2))
        double = d1 == d2
        p = s.players[pid]
        if p.in_jail:
            self._jail_roll(pid, d1 + d2, double)
            return
        if double:
            s.doubles += 1
            if s.doubles == 3:
                self._send_to_jail(pid)
                s.phase = Phase.POST_ROLL
                return
        s.extra_roll = double
        self._move_by(pid, d1 + d2)
        self._resolve(pid)
        self._after_resolution()

    def _jail_roll(self, pid: int, total: int, double: bool) -> None:
        s = self.state
        p = s.players[pid]
        s.extra_roll = False
        if double:
            self._release(pid)
        else:
            p.jail_turns += 1
            if p.jail_turns < MAX_JAIL_TURNS:
                s.phase = Phase.POST_ROLL
                return
            self._release(pid)
            self._deferring = self._payment_windows()
            self._charge(pid, None, JAIL_FINE, reason="jail_fine")
            self._deferring = False
            if s.phase is Phase.RAISE_FUNDS or s.pending_payments:
                s.pending_move = total
                self._after_resolution()
                return
        self._move_by(pid, total)
        self._resolve(pid)
        self._after_resolution()

    def _release(self, pid: int) -> None:
        p = self.state.players[pid]
        p.in_jail = False
        p.jail_turns = 0
        self._emit("jail_release", pid)

    def _pay_jail_fine(self) -> None:
        pid = self.state.current
        self.state.players[pid].cash -= JAIL_FINE
        self._emit("pay", pid, to=None, amount=JAIL_FINE, reason="jail_fine")
        self._release(pid)

    def _use_jail_card(self) -> None:
        s = self.state
        p = s.players[s.current]
        deck = p.jail_cards.pop(0)
        s.decks[deck].append(JAIL_FREE_ID[deck])
        self._release(s.current)

    def _move_by(self, pid: int, steps: int) -> None:
        p = self.state.players[pid]
        new = (p.position + steps) % len(BOARD)
        if new < p.position or (steps > 0 and new == 0):
            self._collect_go(pid)
        p.position = new
        self._emit("move", pid, to=new)

    def _collect_go(self, pid: int) -> None:
        self.state.players[pid].cash += GO_SALARY
        self._emit("pass_go", pid, amount=GO_SALARY)

    def _send_to_jail(self, pid: int) -> None:
        s = self.state
        p = s.players[pid]
        p.position = JAIL_INDEX
        p.in_jail = True
        p.jail_turns = 0
        s.extra_roll = False
        self._emit("jail", pid)

    def _payment_windows(self) -> bool:
        return self.state.config.trade_windows in ("payments", "full") and self.state.window is None

    def _resolve(self, pid: int) -> None:
        self._deferring = self._payment_windows()
        try:
            self._resolve_space(pid)
        finally:
            self._deferring = False

    def _resolve_space(self, pid: int, card_multiplier: str | None = None) -> None:
        s = self.state
        space = BOARD[s.players[pid].position]
        if space.index == GO_TO_JAIL_INDEX:
            self._send_to_jail(pid)
        elif space.index in BUYABLE:
            owner = s.owner[space.index]
            if owner is None:
                s.pending_buy = space.index
                s.phase = Phase.BUY_DECISION
            elif owner != pid:
                dice_total = sum(s.last_roll) if s.last_roll else 0
                rent = rent_for(s, space.index, dice_total, card_multiplier=card_multiplier)
                self._charge(pid, owner, rent, reason="rent", space=space.index)
        elif space.kind is Kind.TAX:
            self._charge(pid, None, space.tax, reason="tax", space=space.index)
        elif space.kind in (Kind.OPPORTUNITY, Kind.COMMUNITY_FUND):
            self._apply_card(pid, draw_card(s, "opportunity" if space.kind is Kind.OPPORTUNITY else "community_fund"))

    def _apply_card(self, pid: int, card: Card) -> None:
        s = self.state
        p = s.players[pid]
        self._emit("card", pid, deck=card.deck, card=card.id, text=card.text)
        e = card.effect
        if e == "advance_to":
            self._move_to(pid, card.target)
            self._resolve_space(pid)
        elif e == "advance_nearest_railroad":
            self._move_to(pid, _nearest(p.position, RAILROADS))
            self._resolve_space(pid, card_multiplier="railroad_double")
        elif e == "advance_nearest_utility":
            self._move_to(pid, _nearest(p.position, UTILITIES))
            space = p.position
            owner = s.owner[space]
            if owner is None:
                s.pending_buy = space
                s.phase = Phase.BUY_DECISION
            elif owner != pid:
                d1, d2 = self.dice.roll()
                self._emit("roll", pid, dice=(d1, d2), for_card=True)
                rent = rent_for(s, space, d1 + d2, card_multiplier="utility_ten")
                self._charge(pid, owner, rent, reason="rent", space=space)
        elif e == "back_three":
            p.position = (p.position - 3) % len(BOARD)
            self._emit("move", pid, to=p.position)
            self._resolve_space(pid)
        elif e == "go_to_jail":
            self._send_to_jail(pid)
        elif e == "jail_free":
            p.jail_cards.append(card.deck)
        elif e == "collect":
            p.cash += card.value
        elif e == "pay":
            self._charge(pid, None, card.value, reason="card")
        elif e == "pay_each":
            for other in self._active_others(pid):
                self._charge(pid, other, card.value, reason="card")
        elif e == "collect_each":
            for other in self._active_others(pid):
                self._charge(other, pid, card.value, reason="card")
        elif e == "repairs":
            houses = sum(b for i, b in enumerate(s.buildings) if s.owner[i] == pid and b < HOTEL)
            hotels = sum(1 for i, b in enumerate(s.buildings) if s.owner[i] == pid and b == HOTEL)
            self._charge(pid, None, houses * card.value + hotels * card.value2, reason="card")

    def _move_to(self, pid: int, target: int) -> None:
        p = self.state.players[pid]
        if target < p.position or target == 0:
            self._collect_go(pid)
        p.position = target
        self._emit("move", pid, to=target)

    def _active_others(self, pid: int) -> list[int]:
        return [q.id for q in self.state.players if q.id != pid and not q.bankrupt]

    def _charge(self, debtor: int, creditor: int | None, amount: int, **info) -> None:
        s = self.state
        if getattr(self, "_deferring", False) and debtor == s.current and amount > 0:
            s.pending_payments.append((debtor, creditor, amount, info))
            return
        for e in charge(s, debtor, creditor, amount):
            self.last_events.append(Event(e.kind, e.player, {**e.data, **info}))

    def _buy(self) -> None:
        s = self.state
        space = s.pending_buy
        s.players[s.current].cash -= BOARD[space].price
        s.owner[space] = s.current
        s.pending_buy = None
        self._emit("buy", s.current, space=space, price=BOARD[space].price)
        s.phase = Phase.POST_ROLL
        self._open_window("acquisition", s.current, "restore", space=space)

    def _decline(self) -> None:
        s = self.state
        space = s.pending_buy
        s.pending_buy = None
        self._emit("decline", s.current, space=space)
        start_auction(s, space, self._next_active(s.current))
        s.phase = Phase.AUCTION

    def _auction(self, action: Action) -> None:
        s = self.state
        events = apply_auction(s, action)
        self.last_events.extend(events)
        if s.auction is not None:
            return
        won = [e for e in events if e.kind == "auction_won"]
        if won:
            self._open_window("acquisition", won[0].player, "continue", space=won[0].data["space"])
        else:
            self._continue_turn()

    def _after_resolution(self) -> None:
        s = self.state
        if s.pending_payments:
            payments, s.pending_payments = s.pending_payments, []
            self._open_window("payment", payments[0][0], "payments", payments=payments,
                              space=payments[0][3].get("space"))
            return
        if s.phase is Phase.RAISE_FUNDS:
            self._settle_debts()
        elif s.phase is Phase.PRE_ROLL:
            s.phase = Phase.POST_ROLL

    def _settle_debts(self) -> None:
        s = self.state
        while s.debts:
            debt = s.debts[0]
            if s.players[debt.debtor].cash >= debt.amount:
                s.debts.pop(0)
                self.last_events.append(pay_debt(s, debt))
                continue
            if self._liquidation(debt.debtor) or self._debt_can_trade(debt):
                s.phase = Phase.RAISE_FUNDS
                return
            self._bankrupt_debtor(debt)
            if self._check_last_player():
                return
        self._continue_turn()

    def _debt_can_trade(self, debt) -> bool:
        s = self.state
        return (s.config.max_trade_exchanges > 0 and debt.trades < s.config.max_trades_per_turn
                and proposal_possible(s, debt.debtor))

    def _bankrupt_debtor(self, debt) -> None:
        s = self.state
        if len(debt_creditors(s, debt.debtor)) > 1:
            self.last_events.extend(split_bankruptcy(s, debt.debtor))
        else:
            self.last_events.extend(declare_bankruptcy(s, debt.debtor, debt.creditor))
        if s.auction is not None:
            self.last_events.extend(remove_bidder(s, debt.debtor))

    def _continue_turn(self) -> None:
        """Unwind interrupted decisions in the order debts, auctions, window, pending move."""
        s = self.state
        if s.debts:
            s.phase = Phase.RAISE_FUNDS
            self._settle_debts()
            return
        held = s.window is not None and s.window.in_auction
        if s.auction is not None and not held:
            s.phase = Phase.AUCTION
            return
        if s.pending_auctions and not held:
            start_auction(s, s.pending_auctions.pop(0), self._next_active(s.current))
            s.phase = Phase.AUCTION
            return
        if s.window is not None:
            if s.trade is not None:
                s.phase, s.resume_phase = Phase.TRADE_RESPONSE, None
                return
            s.phase, s.resume_phase = Phase.TRADE_WINDOW, None
            self._window_step()
            return
        if s.players[s.current].bankrupt:
            s.pending_move = None
            s.pending_buy = None
            self._end_turn()
            return
        if s.pending_move is not None:
            steps, s.pending_move = s.pending_move, None
            s.phase = Phase.PRE_ROLL
            self._move_by(s.current, steps)
            self._resolve(s.current)
            self._after_resolution()
            return
        s.phase = s.resume_phase or Phase.POST_ROLL
        s.resume_phase = None

    def resign(self, player: int) -> list[Event]:
        held = held_jail_cards(self.state)
        events = self._resign(player)
        self._post()
        remember(self.state, events, held=held)
        return events

    def _resign(self, player: int) -> list[Event]:
        s = self.state
        if not 0 <= player < len(s.players) or s.players[player].bankrupt:
            raise IllegalActionError(f"Seat {player} cannot resign.")
        if self.is_over:
            raise IllegalActionError("The game is over.")
        self.last_events = []
        if s.trade is not None and s.trade.resume_phase is Phase.MORTGAGE_TAKEOVER and s.phase is Phase.TRADE_RESPONSE:
            self._close_trade()
        if s.phase is Phase.MORTGAGE_TAKEOVER or s.pending_takeovers or s.takeover_owed:
            self._takeover_flush()
            if self.is_over:
                return self.last_events
        if s.phase is Phase.TRADE_RESPONSE and s.trade.resume_phase in (Phase.RAISE_FUNDS, Phase.AUCTION):
            self._close_trade()
        elif s.trade is not None and player in (s.trade.offer.proposer, s.trade.offer.target):
            if s.phase is Phase.TRADE_RESPONSE:
                self._close_trade()
            else:
                if s.resume_phase is Phase.TRADE_RESPONSE:
                    t = s.trade
                    s.resume_phase = t.outer_resume if t.resume_phase is Phase.RAISE_FUNDS else t.resume_phase
                s.trade = None
        phase_before = s.phase
        self._emit("resign", player)
        if s.auction is not None:
            self.last_events.extend(remove_bidder(s, player))
        self._settle_on_resign(player)
        if not s.players[player].bankrupt:
            s.debts = [d for d in s.debts if d.debtor != player]
            self.last_events.extend(declare_bankruptcy(s, player, None))
        w = s.window
        if w is not None:
            if s.trade is None or not w.queue or w.queue[0] != player:
                w.queue = [q for q in w.queue if q != player]
            w.payments = [p for p in w.payments if p[0] != player]
        s.trade_requests = [q for q in s.trade_requests if q != player]
        if self._check_last_player():
            return self.last_events
        if s.window is not None and s.phase is Phase.TRADE_WINDOW and (s.window.in_auction or not s.pending_auctions):
            if player == s.current:
                s.pending_buy = None
                s.pending_move = None
            self._window_step()
            return self.last_events
        if s.auction is not None:
            if s.phase is Phase.RAISE_FUNDS:
                self._settle_debts()
            return self.last_events
        if player == s.current:
            s.pending_buy = None
            s.pending_move = None
            self._continue_turn()
        elif phase_before is Phase.AUCTION or phase_before is Phase.RAISE_FUNDS:
            self._continue_turn()
        elif s.pending_auctions:
            s.resume_phase = phase_before
            self._continue_turn()
        elif s.phase is Phase.TRADE_WINDOW:
            self._window_step()
        return self.last_events

    def _settle_on_resign(self, player: int) -> None:
        """Settle delayed payments with other debts before surrendering the estate."""
        s = self.state
        w = s.window
        if w is not None:
            own = [p for p in w.payments if p[0] == player]
            if own:
                w.payments = [p for p in w.payments if p[0] != player]
                s.debts.extend(Debt(p[0], p[1], p[2]) for p in own)
        mine = [d for d in s.debts if d.debtor == player]
        if not mine:
            return
        total = sum(d.amount for d in mine)
        if bank_funds(s, player).max_cash >= total:
            while s.players[player].cash < total:
                step = liquidation_step(s, player)
                if step is None:
                    break
                self.last_events.extend(apply_property_action(s, step))
            if s.players[player].cash >= total:
                for d in mine:
                    s.debts.remove(d)
                    self.last_events.append(pay_debt(s, d))
                return
        if len(debt_creditors(s, player)) > 1:
            self.last_events.extend(split_bankruptcy(s, player))
        else:
            self.last_events.extend(declare_bankruptcy(s, player, mine[0].creditor))

    def _liquidation(self, pid: int) -> list[Action]:
        return sell_actions(self.state, pid) + mortgage_actions(self.state, pid)

    def _check_last_player(self) -> bool:
        active = [p.id for p in self.state.players if not p.bankrupt]
        if len(active) == 1:
            self._finish(by_round_limit=False)
            return True
        return False

    def _finish(self, by_round_limit: bool) -> None:
        s = self.state
        s.phase = Phase.GAME_OVER
        s.resume_phase = None
        s.debts = []
        s.pending_auctions = []
        s.auction = None
        s.window = None
        s.pending_payments = []
        s.trade_requests = []
        s.pending_takeovers, s.takeover_owed, s.takeover_return, s.takeover_trades = [], [], None, 0
        s.by_round_limit = by_round_limit
        self._emit("game_over", self.result().winner)

    def result(self) -> GameResult:
        if not self.is_over:
            raise ValueError("The game is still in progress.")
        s = self.state
        worth = tuple(net_worth(s, p.id) for p in s.players)
        active = sorted((p.id for p in s.players if not p.bankrupt), key=lambda i: (-worth[i], i))
        fallen = sorted((p for p in s.players if p.bankrupt), key=lambda p: -p.bankrupt_order)
        ranking = tuple(active) + tuple(p.id for p in fallen)
        limit = s.by_round_limit
        rounds = s.config.max_rounds if limit else s.round
        return GameResult(ranking[0], ranking, rounds, limit, worth)

    def _end_turn(self) -> None:
        s = self.state
        s.resume_phase = None
        prev = s.current
        nxt = self._next_active(prev)
        n = len(s.players)
        if (nxt - s.first_player) % n <= (prev - s.first_player) % n:
            s.round += 1
            if s.config.max_rounds is not None and s.round > s.config.max_rounds:
                self._finish(by_round_limit=True)
                return
        s.current = nxt
        s.turn += 1
        s.doubles = 0
        s.trades_this_turn = 0
        s.requests_this_turn = {}
        s.extra_roll = False
        s.phase = Phase.PRE_ROLL

    def _next_active(self, pid: int) -> int:
        n = len(self.state.players)
        for k in range(1, n + 1):
            cand = (pid + k) % n
            if not self.state.players[cand].bankrupt:
                return cand
        return pid


@dataclass(frozen=True)
class RentcraftResult:
    scores: tuple[int, ...]
    placements: tuple[int, ...]
    reason: str
    details: dict


def public_action(action: Action) -> contracts.Action:
    """Convert an internal choice, including an arbitrary offer, to the shared protocol."""
    kind = action.kind.value
    if action.trade is not None:
        params = observation.visible(action.trade)
        del params["proposer"]
        return contracts.Action(kind, kind, params)
    for name in ("space", "amount"):
        value = getattr(action, name)
        if value is not None:
            return contracts.Action(f"{kind}:{value}", kind, {name: value})
    return contracts.Action(kind, kind, {})


def _command(kind, scope, players):
    properties = {}
    required = []
    if kind in ("propose_trade", "counter_trade"):
        properties["target"] = {"type": "integer", "minimum": 0, "maximum": players - 1, "title": "Recipient seat"}
        if kind == "propose_trade":
            required = ["target"]
        for side in ("give", "get"):
            properties[f"{side}_spaces"] = {"type": "array", "default": [], "uniqueItems": True,
                                            "items": {"type": "integer", "minimum": 0, "maximum": len(BOARD) - 1}}
            for asset in ("cash", "cards"):
                properties[f"{side}_{asset}"] = {"type": "integer", "minimum": 0, "default": 0}
    return {"kind": kind, "scope": scope, "label": describe_action(contracts.Action(kind, kind, {})),
            "params_schema": {"type": "object", "properties": properties, "required": required,
                              "additionalProperties": False}}


def _trade_action(actor, selected, previous):
    params = selected.params
    keys = {"target", "give_spaces", "get_spaces", "give_cash", "get_cash", "give_cards", "get_cards"}
    if selected.id != selected.kind or not isinstance(params, dict) or params.keys() - keys:
        raise contracts.IllegalAction("Invalid trade command")
    target = params.get("target", previous)
    if type(target) is not int or previous is not None and target != previous:
        raise contracts.IllegalAction("Invalid trade recipient")
    values = {}
    for name in ("give_spaces", "get_spaces"):
        spaces = params.get(name, [])
        if type(spaces) is not list or any(type(i) is not int for i in spaces):
            raise contracts.IllegalAction("Expected a list of space indices")
        values[name] = tuple(spaces)
    for name in ("give_cash", "get_cash", "give_cards", "get_cards"):
        amount = params.get(name, 0)
        if type(amount) is not int or amount < 0:
            raise contracts.IllegalAction("Expected a nonnegative integer")
        values[name] = amount
    return Action(ActionKind(selected.kind), trade=TradeOffer(actor, target, **values))


class RentcraftEngine:
    def __init__(self, config, game):
        self._config = contracts.GameConfig(**contracts.config_data(config))
        self.game = game
        self.revision = 0
        self._bootstrap = observation.receipt(game.last_events, 0)
        self._receipt = self._bootstrap
        self.cursor = len(self._bootstrap)

    @property
    def config(self):
        return contracts.GameConfig(**contracts.config_data(self._config))

    @classmethod
    def new(cls, config: contracts.GameConfig, seed: int):
        from .module import game_settings
        return cls(config, Game.new(game_settings(config, seed)))

    def validate_viewer(self, viewer):
        if viewer is not None and (type(viewer) is not int or viewer not in range(self._config.players)):
            raise ValueError("Unknown seat")

    def _require_actor(self, actor):
        if type(actor) is not int or actor not in range(self._config.players) or self.game.state.players[actor].bankrupt:
            raise contracts.WrongPlayer("Expected an active seat")

    def decision(self):
        if self.game.is_over:
            return None
        state = self.game.state
        return contracts.Decision(f"rentcraft:{self.revision}", self.game._decider(), state.phase.value)

    def observe(self, player):
        return observation.observe(self, player)

    def legal_actions(self, player):
        self.validate_viewer(player)
        d = self.decision()
        if d is None or player != d.player:
            return ()
        return tuple(public_action(a) for a in self.game.legal_actions())

    def command_specs(self, viewer):
        self.validate_viewer(viewer)
        d = self.decision()
        if viewer is None or d is None or self.game.state.players[viewer].bankrupt:
            return ()
        commands = []
        if d.player == viewer:
            if self.debt_summary(viewer) and liquidation_step(self.game.state, viewer) is not None:
                commands.append(("liquidate", "decision"))
            if self.game.can_propose():
                commands.append(("propose_trade", "decision"))
            if self.game.can_counter():
                commands.append(("counter_trade", "decision"))
        if self.game.can_request_trade(viewer):
            commands.append(("request_trade", "seat"))
        commands.append(("resign", "seat"))
        return tuple(_command(k, scope, self._config.players) for k, scope in commands)

    def debt_summary(self, viewer):
        """What the bank can raise for the viewer's debt (only while the viewer must raise funds), else None."""
        s = self.game.state
        if viewer is None or s.phase is not Phase.RAISE_FUNDS or not s.debts or s.debts[0].debtor != viewer:
            return None
        amount = sum(d.amount for d in s.debts if d.debtor == viewer)
        funds = bank_funds(s, viewer)
        return {"amount": amount, "cash": funds.cash, "mortgage_now": funds.mortgage_now,
                "building_sales": funds.building_sales, "mortgage_after_sales": funds.mortgage_after_sales,
                "max_cash_from_bank": funds.max_cash, "bank_enough": funds.max_cash >= amount,
                "needed_from_trades": max(0, amount - funds.max_cash)}

    def _liquidate(self, player):
        """Mortgages first, then building sales at half price, until the debt is paid or the bank has nothing more."""
        s, events = self.game.state, []
        while s.phase is Phase.RAISE_FUNDS and s.debts and s.debts[0].debtor == player:
            step = liquidation_step(s, player)
            if step is None or step not in self.game.legal_actions():
                break
            events.extend(self.game.apply(step))
        if not events:
            raise contracts.IllegalAction("The bank has nothing more to give")
        return events

    def _accept(self, events):
        self._receipt = observation.receipt(events, self.cursor)
        self.cursor += len(events)
        self.revision += 1
        return self._detached(self._receipt)

    def apply(self, player, action, decision_id):
        self._require_actor(player)
        d = self.decision()
        if d is None or decision_id != d.id:
            raise contracts.StaleDecision("This decision has expired")
        if player != d.player:
            raise contracts.WrongPlayer("Another seat holds this decision")
        if not isinstance(action, contracts.Action) or type(action.params) is not dict:
            raise contracts.IllegalAction("Invalid decision command")
        if action.kind == "liquidate":
            if action.params != {} or not self.debt_summary(player):
                raise contracts.IllegalAction("A bank liquidation is unavailable")
            return self._accept(self._liquidate(player))
        if action.kind in ("propose_trade", "counter_trade"):
            previous = None
            if action.kind == "counter_trade":
                if not self.game.can_counter():
                    raise contracts.IllegalAction("A counteroffer is unavailable")
                previous = self.game.state.trade.offer.proposer
            selected = _trade_action(player, action, previous)
        else:
            if any(type(x) is not int for x in action.params.values()):
                raise contracts.IllegalAction("Invalid action parameters")
            selected = next((a for a in self.game.legal_actions() if public_action(a) == action), None)
            if selected is None:
                raise contracts.IllegalAction("The action is unavailable in this decision")
        try:
            events = self.game.apply(selected)
        except IllegalActionError as error:
            raise contracts.IllegalAction(str(error)) from error
        return self._accept(events)

    def apply_seat_operation(self, actor, action, expected_revision):
        self._require_actor(actor)
        if type(expected_revision) is not int or expected_revision != self.revision:
            raise contracts.StaleDecision("The position has changed")
        if (self.game.is_over or not isinstance(action, contracts.Action) or action.id != action.kind
                or action.params != {} or action.kind not in ("resign", "request_trade")):
            raise contracts.IllegalAction("Invalid seat operation")
        try:
            events = self.game.resign(actor) if action.kind == "resign" else self.game.request_trade(actor)
        except IllegalActionError as error:
            raise contracts.IllegalAction(str(error)) from error
        return self._accept(events)

    @staticmethod
    def _detached(events):
        return tuple(contracts.Event(e.sequence, e.kind, e.payload) for e in events)

    def initial_events(self):
        return self._detached(self._bootstrap)

    def project_events(self, events, viewer):
        self.validate_viewer(viewer)
        if type(events) is not tuple or not all(isinstance(e, contracts.Event) for e in events):
            raise ValueError("Expected an exact engine receipt")
        # Return only entries constructed by the engine, even if the caller supplied lookalikes.
        for known in (self._receipt, self._bootstrap):
            if events == known:
                return self._detached(known)
        raise ValueError("Unknown or stale receipt")

    def is_over(self):
        return self.game.is_over

    def result(self):
        if not self.is_over():
            return None
        outcome = self.game.result()
        placements = tuple(outcome.ranking.index(i) + 1 for i in range(self._config.players))
        return RentcraftResult(outcome.net_worth, placements, "round_limit" if outcome.by_round_limit else "last_player",
                               {"winner": outcome.winner, "rounds": outcome.rounds, "by_round_limit": outcome.by_round_limit})

    def clone(self):
        return copy.deepcopy(self)

    def fork(self, seed):
        if type(seed) is not int:
            raise ValueError("A fork seed must be an integer")
        fork = self.clone()
        game = fork.game
        game.rng = random.Random(seed)
        game.dice = RandomDice(game.rng)
        s = game.state
        s.decks = observation.sample_decks(s.players, s.memory.known_bottom, game.rng)
        return fork

    def snapshot(self):
        return {"format": 1, "game_id": "rentcraft", "config": contracts.config_data(self._config),
                "revision": self.revision, "cursor": self.cursor, "state": persistence.plain(self.game.state),
                "chance": persistence.chance_data(self.game), "bootstrap": persistence.plain(self._bootstrap),
                "receipt": persistence.plain(self._receipt)}

    @classmethod
    def restore(cls, snapshot):
        from .module import game_settings
        try:
            if (type(snapshot) is not dict or snapshot.keys() !=
                {"format", "game_id", "config", "revision", "cursor", "state", "chance", "bootstrap", "receipt"}
                    or type(snapshot["format"]) is not int or snapshot["format"] != 1 or snapshot["game_id"] != "rentcraft"):
                raise ValueError("Unknown Rentcraft snapshot format")
            config = contracts.GameConfig(**snapshot["config"])
            state = persistence.decode(GameState, snapshot["state"])
            if state.config != game_settings(config, state.config.seed):
                raise ValueError("Conflicting game settings")
            persistence.validate_position(state)
            rng, dice = persistence.restore_chance(snapshot["chance"])
            engine = cls(config, Game(state, rng, dice))
            engine.revision = persistence.decode(int, snapshot["revision"])
            engine.cursor = persistence.decode(int, snapshot["cursor"])
            if engine.revision < 0 or engine.cursor < 0:
                raise ValueError("Invalid event counters")
            engine._bootstrap = _restore_receipt(snapshot["bootstrap"], config.players)
            engine._receipt = _restore_receipt(snapshot["receipt"], config.players)
            if (not engine._bootstrap or engine._bootstrap[-1].sequence > engine.cursor
                    or engine._bootstrap[0].sequence != 1
                    or engine._receipt and engine._receipt[-1].sequence != engine.cursor
                    or engine.revision == 0 and engine._bootstrap != engine._receipt):
                raise ValueError("Invalid receipt cursors")
            engine.decision()
            return engine
        except (KeyError, IndexError, AttributeError, TypeError, OverflowError, ValueError) as error:
            raise ValueError("Invalid Rentcraft snapshot") from error


def _restore_receipt(rows, seats):
    if type(rows) is not list:
        raise ValueError("Expected a receipt list")
    result = []
    for row in rows:
        event = contracts.Event(**row)
        observation.validate_saved_payload(event.kind, event.payload, seats)
        if (type(event.sequence) is not int or event.sequence < 1
                or result and event.sequence != result[-1].sequence + 1):
            raise ValueError("Invalid saved event sequence")
        result.append(event)
    return tuple(result)
