"""Construct and value trades, including sales to raise cash and settle debts."""
import copy
from typing import Callable

from ..board import BOARD, GROUPS, Kind, RAILROADS, UTILITIES
from ..actions import ActionKind, Decision, Phase
from ..buildings import apply_property_action, mortgage_actions, sell_actions, unmortgage_cost
from ..memory import sent_this_turn
from ..state import GameState
from ..trade import TradeOffer, can_propose, validate_offer


BankChoice = Callable[[GameState, Decision], object]


SET_BONUS = 1.5

RIVAL_WEIGHT = 0.6

BUYER_RESERVE = 150


_PRICE = [s.price for s in BOARD]

_SET_BONUSES = [(frozenset(spaces), SET_BONUS * sum(BOARD[s].price for s in spaces)) for spaces in GROUPS.values()]
_RAIL_BONUS = (0, 0, 60, 180, 400)
_UTILS = frozenset(UTILITIES)
_GROUP_BONUS = {g: SET_BONUS * sum(BOARD[s].price for s in spaces) for g, spaces in GROUPS.items()}


def holdings_value(owned: set[int]) -> float:
    v = float(sum(_PRICE[i] for i in owned))
    for spaces, bonus in _SET_BONUSES:
        if spaces <= owned:
            v += bonus
    v += _RAIL_BONUS[sum(1 for s in RAILROADS if s in owned)]
    if _UTILS <= owned:
        v += 60
    return v


def field_value(state: GameState, pid: int, space: int) -> float:
    s = BOARD[space]
    v = float(s.price)
    owner = state.owner
    if s.group is not None:
        if all(owner[i] == pid for i in GROUPS[s.group] if i != space):
            v += _GROUP_BONUS[s.group]
    elif space in RAILROADS:
        k = sum(1 for i in RAILROADS if i != space and owner[i] == pid)
        v += _RAIL_BONUS[k + 1] - _RAIL_BONUS[k]
    elif space in _UTILS:
        if all(owner[i] == pid for i in UTILITIES if i != space):
            v += 60
    return v


def _owned(state: GameState, pid: int) -> set[int]:
    return {i for i, o in enumerate(state.owner) if o == pid}


def _score(state: GameState, offer: TradeOffer, me: int, mine: set[int], theirs: set[int], value) -> float:
    give_sp, get_sp = (offer.give_spaces, offer.get_spaces) if me == offer.proposer else (offer.get_spaces, offer.give_spaces)
    give_cash, get_cash = (offer.give_cash, offer.get_cash) if me == offer.proposer else (offer.get_cash, offer.give_cash)
    give_c, get_c = (offer.give_cards, offer.get_cards) if me == offer.proposer else (offer.get_cards, offer.give_cards)
    mine_after = (mine - set(give_sp)) | set(get_sp)
    theirs_after = (theirs - set(get_sp)) | set(give_sp)

    def mortgage_cost(spaces):
        return sum(BOARD[s].mortgage_value * 1.1 for s in spaces if state.mortgaged[s])

    my_gain = value(mine_after) - value(mine) + get_cash - give_cash + 50 * (get_c - give_c)
    my_gain += mortgage_cost(give_sp) - mortgage_cost(get_sp)
    rival_gain = value(theirs_after) - value(theirs) + give_cash - get_cash + 50 * (give_c - get_c)
    return my_gain - RIVAL_WEIGHT * rival_gain


def trade_score(state: GameState, offer: TradeOffer, me: int) -> float:
    other = offer.target if me == offer.proposer else offer.proposer
    return _score(state, offer, me, _owned(state, me), _owned(state, other), holdings_value)


def _balance(score: float) -> int:
    if score > 0:
        return 0
    need = int(-score / (1 + RIVAL_WEIGHT)) + 10
    return (need + 9) // 10 * 10


def cash_to_balance(state: GameState, offer: TradeOffer, me: int) -> int | None:
    return _balance(trade_score(state, offer, me))


_EPS = 1e-6

_BALANCE_SLACK = 9


class Motives:
    def __init__(self, state: GameState):
        self.state = state
        self._owned: dict[int, set[int]] = {}
        self._values: dict[frozenset, float] = {}
        self._scores: dict[tuple[TradeOffer, int], float] = {}

    def owned(self, pid: int) -> set[int]:
        o = self._owned.get(pid)
        if o is None:
            o = self._owned[pid] = _owned(self.state, pid)
        return o

    def value(self, spaces) -> float:
        key = frozenset(spaces)
        v = self._values.get(key)
        if v is None:
            v = self._values[key] = holdings_value(key)
        return v

    def score(self, offer: TradeOffer, me: int) -> float:
        key = (offer, me)
        s = self._scores.get(key)
        if s is None:
            other = offer.target if me == offer.proposer else offer.proposer
            s = self._scores[key] = _score(self.state, offer, me, self.owned(me), self.owned(other), self.value)
        return s

    def sale_scores(self, buyer: int, seller: int, space: int) -> tuple[float, float]:
        gain, loss = field_value(self.state, buyer, space), field_value(self.state, seller, space)
        fee = BOARD[space].mortgage_value * 1.1 if self.state.mortgaged[space] else 0.0
        return gain - fee + RIVAL_WEIGHT * loss, fee - loss - RIVAL_WEIGHT * gain


def proposals(state: GameState, me: int, reserve: int) -> list[TradeOffer]:
    out: list[tuple[float, TradeOffer]] = []
    cash = state.players[me].cash
    ctx = Motives(state)
    mine = ctx.owned(me)
    for group, spaces in GROUPS.items():
        missing = [s for s in spaces if s not in mine]
        if len(missing) != 1:
            continue
        m = missing[0]
        x = state.owner[m]
        if x is None or x == me or state.players[x].bankrupt:
            continue

        theirs = ctx.owned(x)
        for q in mine:
            g = BOARD[q].group
            if not g or g == group:
                continue
            if all(s in theirs or s == q for s in GROUPS[g]):
                offer = TradeOffer(me, x, give_spaces=(q,), get_spaces=(m,))
                extra = _balance(ctx.score(offer, x)) or 0
                if extra and extra <= cash - reserve:
                    offer = TradeOffer(me, x, give_spaces=(q,), get_spaces=(m,), give_cash=extra)
                if validate_offer(state, offer) is None and ctx.score(offer, me) > 0 and ctx.score(offer, x) > 0:
                    out.append((ctx.score(offer, me), offer))

        mine_s, theirs_s = ctx.sale_scores(me, x, m)
        for mult in (1.5, 2.0, 3.0):
            c = int(BOARD[m].price * mult) // 10 * 10
            if c > cash - reserve:
                break
            if min(mine_s - c * (1 + RIVAL_WEIGHT), theirs_s + c * (1 + RIVAL_WEIGHT)) < -_EPS:
                continue
            offer = TradeOffer(me, x, give_cash=c, get_spaces=(m,))
            if validate_offer(state, offer) is None and ctx.score(offer, me) > 0 and ctx.score(offer, x) > 0:
                out.append((ctx.score(offer, me), offer))
                break
    out.sort(key=lambda t: -t[0])
    return [o for _, o in out]


def payer_purchases(state: GameState, me: int, reserve: int) -> list[TradeOffer]:
    w = state.window
    if w is None or w.kind != 'payment' or w.trigger is None or w.trigger == me:
        return []
    payer = w.trigger
    if state.players[payer].bankrupt:
        return []
    cash = state.players[me].cash
    ctx = Motives(state)
    out: list[tuple[float, int, TradeOffer]] = []
    for sp in sorted(ctx.owned(payer)):
        group = BOARD[sp].group
        if group and any(state.buildings[j] for j in GROUPS[group]):
            continue
        mine_s, payer_s = ctx.sale_scores(me, payer, sp)
        if mine_s < -_EPS:
            continue
        if payer_s < -_EPS:
            low = -payer_s / (1 + RIVAL_WEIGHT) + _BALANCE_SLACK
            if low - _EPS >= cash - reserve or mine_s + payer_s - _BALANCE_SLACK * (1 + RIVAL_WEIGHT) < -_EPS:
                continue
        probe = TradeOffer(me, payer, get_spaces=(sp,))
        if ctx.score(probe, me) <= 0:
            continue
        price = _balance(ctx.score(probe, payer)) or 0
        if price > cash - reserve:
            continue
        offer = TradeOffer(me, payer, give_cash=price, get_spaces=(sp,))
        score = ctx.score(offer, me)
        if score > 0 and ctx.score(offer, payer) > 0 and validate_offer(state, offer) is None:
            out.append((score, -sp, offer))
    out.sort(key=lambda t: (-t[0], -t[1]))
    return [o for *_, o in out]


def counter_for(state: GameState, offer: TradeOffer, me: int) -> TradeOffer | None:
    need = cash_to_balance(state, offer, me)
    if not need:
        return None
    other = offer.proposer
    if state.players[other].cash < need + (offer.give_cash if offer.proposer == other else 0):
        return None

    counter = TradeOffer(
        me, other,
        give_spaces=offer.get_spaces, get_spaces=offer.give_spaces,
        give_cash=offer.get_cash, get_cash=offer.give_cash + need,
        give_cards=offer.get_cards, get_cards=offer.give_cards,
    )
    if validate_offer(state, counter) is not None or trade_score(state, counter, me) <= 0:
        return None
    return counter


def takeover_payoff(state: GameState, me: int, space: int, reserve: int) -> bool:
    if state.players[me].cash - unmortgage_cost(space) < reserve:
        return False
    s = BOARD[space]
    if s.group is not None:
        mine = sum(1 for i in GROUPS[s.group] if state.owner[i] == me)
        return mine >= 2 and len(GROUPS[s.group]) - mine <= 1
    same = RAILROADS if s.kind is Kind.RAILROAD else UTILITIES
    return sum(1 for i in same if state.owner[i] == me) >= 2


def mortgage_capacity(state: GameState, me: int) -> int:
    total = 0
    for i, o in enumerate(state.owner):
        if o != me or state.mortgaged[i]:
            continue
        g = BOARD[i].group
        if g and any(state.buildings[j] for j in GROUPS[g]):
            continue
        total += BOARD[i].mortgage_value
    return total


def spending_cash(state: GameState, me: int, share: float) -> int:
    return state.players[me].cash + int(share * mortgage_capacity(state, me))


def field_worth_raising(state: GameState, me: int, space: int) -> bool:
    s = BOARD[space]
    mine = _owned(state, me)
    if holdings_value(mine | {space}) - holdings_value(mine) > s.price:
        return True
    if s.group is not None:
        others = [state.owner[i] for i in GROUPS[s.group] if i != space]
        if me in others and all(o in (None, me) for o in others):
            return True
    for rival, p in enumerate(state.players):
        if rival == me or p.bankrupt:
            continue
        theirs = _owned(state, rival)
        if holdings_value(theirs | {space}) - holdings_value(theirs) > s.price and s.group is not None:
            return True
    return False


def _loose(state: GameState, me: int, space: int, sp: int) -> bool:
    g = BOARD[sp].group
    return sp != space and not (g is not None and (g == BOARD[space].group or all(state.owner[i] == me for i in GROUPS[g])))


def raise_mortgage(state: GameState, me: int, need: int, space: int, actions) -> object:
    cash = state.players[me].cash
    loose = [a for a in actions if a.kind is ActionKind.MORTGAGE and _loose(state, me, space, a.space)]
    if not loose or cash + sum(BOARD[a.space].mortgage_value for a in loose) < need:
        return None

    def base_rent(a):
        s = BOARD[a.space]
        return (s.rents[0] if s.kind is Kind.PROPERTY else 25, a.space)

    return min(loose, key=base_rent)


def buy_raise_mortgage(state: GameState, me: int, space: int, actions) -> object:
    return raise_mortgage(state, me, BOARD[space].price, space, actions)


def takeover_set_field(state: GameState, me: int, space: int) -> bool:
    group = BOARD[space].group
    return group is not None and all(state.owner[i] == me for i in GROUPS[group])


def raise_sales(state: GameState, me: int, need: int, space: int) -> list[TradeOffer]:
    if state.trade is not None or not can_propose(state):
        return []
    cash = state.players[me].cash
    if cash >= need:
        return []
    debtors = {d.debtor for d in state.debts}
    buyers = [i for i, p in enumerate(state.players) if i != me and not p.bankrupt and i not in debtors]
    ctx = Motives(state)
    mine = ctx.owned(me)
    loose = [sp for sp in sorted(mine) if _loose(state, me, space, sp)
             and not any(state.buildings[j] for j in GROUPS.get(BOARD[sp].group, ()))]
    capacity = {sp: (0 if state.mortgaged[sp] else BOARD[sp].mortgage_value) for sp in loose}
    out: list[tuple[int, int, int, TradeOffer]] = []
    for sp in loose:
        if ctx.value(mine) - ctx.value(mine - {sp}) - BOARD[sp].price > 0:
            continue
        bank = capacity[sp]
        rest = sum(capacity.values()) - bank
        for x in buyers:
            if _completes_for(state, x, sp):
                continue
            floor = (bank + 10 + 9) // 10 * 10
            top = _buyer_top(ctx, me, x, sp, floor)
            if top < floor:
                continue
            price = max(floor, (bank + (top - bank) // 2) // 10 * 10)
            if cash + price + rest < need:
                continue
            offer = TradeOffer(me, x, give_spaces=(sp,), get_cash=price)
            if validate_offer(state, offer) is None and not sent_this_turn(state, offer):
                out.append((price - bank, -sp, -x, offer))
    out.sort(key=lambda t: (-t[0], -t[1], -t[2]))
    return [o for *_, o in out]


def liquidation_cash(state: GameState, me: int) -> int:
    cash = state.players[me].cash
    for i, o in enumerate(state.owner):
        if o == me:
            cash += state.buildings[i] * (BOARD[i].house_cost // 2) + (0 if state.mortgaged[i] else BOARD[i].mortgage_value)
    return cash


def _buyer_top(ctx: Motives, me: int, buyer: int, space: int, floor: int) -> int:
    limit = ctx.state.players[buyer].cash - BUYER_RESERVE
    if limit // 10 * 10 < floor:
        return limit // 10 * 10
    probe = TradeOffer(me, buyer, give_spaces=(space,))
    top = int(ctx.score(probe, buyer) / (1 + RIVAL_WEIGHT))
    return min(top, limit) // 10 * 10


def _completes_for(state: GameState, buyer: int, space: int) -> bool:
    group = BOARD[space].group
    return group is not None and all(state.owner[i] == buyer for i in GROUPS[group] if i != space)


def _scratch(state: GameState) -> GameState:
    s = copy.copy(state)
    s.owner, s.buildings, s.mortgaged = list(state.owner), list(state.buildings), list(state.mortgaged)
    s.players = [copy.copy(p) for p in state.players]
    return s


def bank_route_sells_buildings(state: GameState, me: int, owed: int, choose: BankChoice) -> bool:
    s = _scratch(state)
    for _ in range(200):
        if s.players[me].cash >= owed:
            return False
        acts = tuple(sell_actions(s, me) + mortgage_actions(s, me))
        if not acts:
            return False
        a = choose(s, Decision(me, Phase.RAISE_FUNDS, acts)).action
        if a.kind is ActionKind.SELL_BUILDING:
            return True
        apply_property_action(s, a)
    return False


def _sale_avoids_buildings(state: GameState, me: int, owed: int, choose: BankChoice, offer: TradeOffer) -> bool:
    s = _scratch(state)
    sp = offer.give_spaces[0]
    s.owner[sp] = offer.target
    s.players[me].cash += offer.get_cash
    return not bank_route_sells_buildings(s, me, owed, choose)


def debtor_sales(state: GameState, me: int, bank_choice: BankChoice | None = None) -> list[TradeOffer]:
    if state.trade is not None or not can_propose(state):
        return []
    owed = sum(d.amount for d in state.debts if d.debtor == me)
    if owed <= state.players[me].cash:
        return []
    desperate = liquidation_cash(state, me) < owed

    spare_buildings = bank_choice is not None and not desperate
    if spare_buildings and not bank_route_sells_buildings(state, me, owed, bank_choice):
        return []
    debtors = {d.debtor for d in state.debts}
    buyers = [i for i, p in enumerate(state.players) if i != me and not p.bankrupt and i not in debtors]
    ctx = Motives(state)
    mine = ctx.owned(me)
    out: list[tuple[int, int, int, TradeOffer]] = []
    for sp in sorted(mine):
        group = BOARD[sp].group
        if group and any(state.buildings[j] for j in GROUPS[group]):
            continue

        breaks = ctx.value(mine) - ctx.value(mine - {sp}) - BOARD[sp].price > 0
        if breaks and not desperate:
            continue
        bank = 0 if state.mortgaged[sp] else BOARD[sp].mortgage_value
        for x in buyers:
            if _completes_for(state, x, sp) and not desperate:
                continue
            floor = (bank + 10 + 9) // 10 * 10
            top = _buyer_top(ctx, me, x, sp, floor)
            if top < floor:
                continue
            price = top if desperate else max(floor, (bank + (top - bank) // 2) // 10 * 10)
            offer = TradeOffer(me, x, give_spaces=(sp,), get_cash=price)
            if spare_buildings and not _sale_avoids_buildings(state, me, owed, bank_choice, offer):
                continue
            if validate_offer(state, offer) is None and not sent_this_turn(state, offer):
                out.append((price - bank, -sp, -x, offer))
    out.sort(key=lambda t: (-t[0], -t[1], -t[2]))
    return [o for *_, o in out]
