"""Price partial progress towards a group as well as the eventual completed set."""
from dataclasses import dataclass
from typing import Sequence

from ..board import BOARD, GROUPS, RAILROADS
from ..actions import Action, ActionKind, Decision
from ..state import GameState
from ..trade import TradeOffer, validate_offer
from . import Choice
from .calculator import CalculatorBot, CalculatorParams
from .profiles import PROFILES
from .trading import _GROUP_BONUS, _RAIL_BONUS, RIVAL_WEIGHT, _balance, holdings_value, trade_score

PROFILE = PROFILES['wary-calculator']


@dataclass(frozen=True)
class WaryParams(CalculatorParams):
    near_share: float = 1.0
    far_share: float = 1.0

    rail_share: float = 2.0

    progress_factor: float = 1.25


WARY_PARAMS = WaryParams()


def _potential(owner: Sequence[int | None], pid: int, params: WaryParams) -> float:
    v = 0.0
    for g, spaces in GROUPS.items():
        n = 0
        for s in spaces:
            o = owner[s]
            if o == pid:
                n += 1
            elif o is not None:
                break
        else:
            missing = len(spaces) - n
            if n and missing:
                v += (params.near_share if missing == 1 else params.far_share) * _GROUP_BONUS[g]
    if params.rail_share:
        n = sum(1 for s in RAILROADS if owner[s] == pid)
        if n < len(RAILROADS):
            v += params.rail_share * n * _RAIL_BONUS[-1] / len(RAILROADS)
    return v


def progress_value(owner: Sequence[int | None], pid: int, params: WaryParams = WARY_PARAMS) -> float:
    return holdings_value({i for i, o in enumerate(owner) if o == pid}) + _potential(owner, pid, params)


def _after(state: GameState, offer: TradeOffer) -> list[int | None]:
    owner = list(state.owner)
    for s in offer.give_spaces:
        owner[s] = offer.target
    for s in offer.get_spaces:
        owner[s] = offer.proposer
    return owner


def progress_score(state: GameState, offer: TradeOffer, me: int, params: WaryParams = WARY_PARAMS) -> float:
    proposer = me == offer.proposer
    other = offer.target if proposer else offer.proposer
    give_sp, get_sp = (offer.give_spaces, offer.get_spaces) if proposer else (offer.get_spaces, offer.give_spaces)
    give_cash, get_cash = (offer.give_cash, offer.get_cash) if proposer else (offer.get_cash, offer.give_cash)
    give_c, get_c = (offer.give_cards, offer.get_cards) if proposer else (offer.get_cards, offer.give_cards)
    before, after = state.owner, _after(state, offer)

    def mortgage_cost(spaces):
        return sum(BOARD[s].mortgage_value * 1.1 for s in spaces if state.mortgaged[s])

    my_gain = progress_value(after, me, params) - progress_value(before, me, params)
    my_gain += get_cash - give_cash + 50 * (get_c - give_c) + mortgage_cost(give_sp) - mortgage_cost(get_sp)
    rival_gain = progress_value(after, other, params) - progress_value(before, other, params)
    rival_gain += give_cash - get_cash + 50 * (give_c - get_c)
    return my_gain - RIVAL_WEIGHT * rival_gain


def gives_progress(state: GameState, offer: TradeOffer, me: int, params: WaryParams = WARY_PARAMS) -> bool:
    proposer = me == offer.proposer
    if not (offer.give_spaces if proposer else offer.get_spaces):
        return False
    other = offer.target if proposer else offer.proposer
    after = _after(state, offer)
    gain = _potential(after, other, params) - _potential(state.owner, other, params)
    full = sum(1 for spaces in GROUPS.values() if all(after[s] == other for s in spaces))
    was = sum(1 for spaces in GROUPS.values() if all(state.owner[s] == other for s in spaces))
    return gain > 1e-6 or full > was


def feeds_progress(state: GameState, me: int, space: int) -> bool:
    group = BOARD[space].group
    if group is None:
        return False
    others = [state.owner[i] for i in GROUPS[group] if i != space]
    holders = {o for o in others if o is not None}
    if len(holders) != 1:
        return False
    rival = holders.pop()
    return rival != me and not state.players[rival].bankrupt


class WaryBot(CalculatorBot):
    def __init__(self):
        super().__init__()
        self.profile = PROFILE
        self.label = PROFILE.id
        self.params = WARY_PARAMS

    def _score(self, state: GameState, offer: TradeOffer, me: int) -> float:
        return min(trade_score(state, offer, me), progress_score(state, offer, me, self.params))

    def _buy(self, state: GameState, d: Decision) -> Choice:
        me = d.player
        space = state.pending_buy
        buy = Action(ActionKind.BUY)
        left = state.players[me].cash - BOARD[space].price
        if buy in d.actions and feeds_progress(state, me, space) and left >= self.params.min_reserve:
            return Choice(buy, f'Buy to prevent a rival collecting the remaining group from the bank, leaving ${left}')
        return super()._buy(state, d)

    def _limit(self, state: GameState, me: int, space: int) -> tuple[int, str]:
        limit, why = super()._limit(state, me, space)
        if feeds_progress(state, me, space):
            s = BOARD[space]
            cash = self._auction_cash(state, me) - self._reserve(state, me)
            mine = int(min(s.price * self.params.progress_factor, cash))
            if mine > limit:
                return mine, 'prevent a rival from collecting an uncontested group'
        return limit, why

    def _offer_ok(self, state: GameState, offer: TradeOffer) -> bool:
        me = offer.proposer
        return not gives_progress(state, offer, me, self.params) or progress_score(state, offer, me, self.params) > 0

    def _refused(self, state: GameState, d: Decision, a: Action) -> bool:
        if super()._refused(state, d, a):
            return True
        if a.kind is not ActionKind.ACCEPT_TRADE:
            return False
        offer = state.trade.offer
        return gives_progress(state, offer, d.player, self.params) and self._score(state, offer, d.player) <= 0

    def _counter(self, state: GameState, offer: TradeOffer, me: int, score: float) -> TradeOffer | None:
        need = _balance(score)
        other = offer.proposer
        if not need or state.players[other].cash < need + offer.give_cash:
            return None
        counter = TradeOffer(
            me, other,
            give_spaces=offer.get_spaces, get_spaces=offer.give_spaces,
            give_cash=offer.get_cash, get_cash=offer.give_cash + need,
            give_cards=offer.get_cards, get_cards=offer.give_cards,
        )
        if validate_offer(state, counter) is not None or self._score(state, counter, me) <= 0:
            return None
        return counter

    def _respond(self, state: GameState, d: Decision) -> Choice:
        feed = self._feed_response(state, d)
        if feed is not None:
            return feed
        offer = state.trade.offer
        me = d.player
        common = trade_score(state, offer, me)
        score = self._score(state, offer, me)
        piecewise = score < common and gives_progress(state, offer, me, self.params)
        left = state.players[me].cash - (offer.get_cash if me == offer.target else offer.give_cash)
        if score > self._accept_shift(offer) and left >= self.profile.buy_reserve // 2:
            return Choice(Action(ActionKind.ACCEPT_TRADE), f'Accept the trade, score {score:+.0f}')
        why = (f"The property advances the rival's color or station set, score {score:.0f}"
               if piecewise else f'score {score:.0f}')
        if state.trade.exchanges < state.config.max_trade_exchanges:
            counter = self._counter(state, offer, me, score)
            if counter is not None:
                cash = state.players[offer.proposer].cash
                counter = self._vary(state, counter, me, lambda o: self._score(state, o, me) > 0 and o.get_cash <= cash)
                return Choice(Action(ActionKind.COUNTER_TRADE, trade=counter), f'Counteroffer: {why}, requesting a cash adjustment')
        return Choice(Action(ActionKind.REJECT_TRADE), f'Reject the trade. {why}')
