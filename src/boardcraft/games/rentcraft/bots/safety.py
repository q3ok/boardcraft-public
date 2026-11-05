"""Estimate exposure, liquidity and the value a trade gives to another seat."""
from functools import lru_cache

from ..board import BID_STEPS, BOARD, GROUPS
from ..actions import Action, ActionKind
from ..state import GameState
from ..rent import rent_for
from ..trade import TradeOffer
from .odds import roll_probs
from .trading import SET_BONUS, holdings_value, liquidation_cash


FEED_SET_BONUS = 2.0
NEAR_SHARE = 1 / 3
_GROUP_PRICE = {g: sum(BOARD[s].price for s in spaces) for g, spaces in GROUPS.items()}


def _owned(state: GameState, pid: int) -> set[int]:
    return {i for i, o in enumerate(state.owner) if o == pid}


def _progress(state: GameState, before: set[int], after: set[int]) -> float:
    v = holdings_value(after) - holdings_value(before)
    for g, spaces in GROUPS.items():
        n0 = sum(1 for s in spaces if s in before)
        n1 = sum(1 for s in spaces if s in after)
        if n0 == n1:
            continue
        bonus = FEED_SET_BONUS * _GROUP_PRICE[g]
        full = len(spaces)
        v += (bonus - SET_BONUS * _GROUP_PRICE[g]) * ((n1 == full) - (n0 == full))
        if full == 3:
            v += NEAR_SHARE * bonus * ((n1 >= 2) - (n0 >= 2))
    return v


def _sides(offer: TradeOffer, me: int):
    if me == offer.proposer:
        return offer.target, offer.give_spaces, offer.get_spaces, offer.give_cash, offer.get_cash, offer.give_cards, offer.get_cards
    return offer.proposer, offer.get_spaces, offer.give_spaces, offer.get_cash, offer.give_cash, offer.get_cards, offer.give_cards


def feeding(state: GameState, offer: TradeOffer, me: int) -> bool:
    other, give_sp, *_ = _sides(offer, me)
    if not give_sp:
        return False
    theirs = _owned(state, other)
    for g in {BOARD[s].group for s in give_sp if BOARD[s].group}:
        spaces = GROUPS[g]
        n0 = sum(1 for s in spaces if s in theirs)
        n1 = n0 + sum(1 for s in give_sp if BOARD[s].group == g)
        if n1 == len(spaces) or (len(spaces) == 3 and n1 == 2 and n0 < 2):
            return True
    return False


def feed_gains(state: GameState, offer: TradeOffer, me: int) -> tuple[float, float]:
    other, give_sp, get_sp, give_cash, get_cash, give_c, get_c = _sides(offer, me)
    mine, theirs = _owned(state, me), _owned(state, other)
    mine_after = (mine - set(give_sp)) | set(get_sp)
    theirs_after = (theirs - set(get_sp)) | set(give_sp)

    def mortgage_cost(spaces):
        return sum(BOARD[s].mortgage_value * 1.1 for s in spaces if state.mortgaged[s])

    my_gain = _progress(state, mine, mine_after) + get_cash - give_cash + 50 * (get_c - give_c)
    my_gain += mortgage_cost(give_sp) - mortgage_cost(get_sp)
    their_gain = _progress(state, theirs, theirs_after) + give_cash - get_cash + 50 * (give_c - get_c)
    their_gain += mortgage_cost(get_sp) - mortgage_cost(give_sp)
    return my_gain, their_gain


def feeds_rival(state: GameState, offer: TradeOffer, me: int, ratio: float) -> bool:
    if not feeding(state, offer, me):
        return False
    mine, theirs = feed_gains(state, offer, me)
    return theirs > ratio * max(mine, 0.0)


def feed_price(state: GameState, offer: TradeOffer, me: int, ratio: float) -> int:
    mine, theirs = feed_gains(state, offer, me)
    need = max((theirs - ratio * mine) / (1 + ratio), -mine, 0.0)
    return (int(need) // 10 + 1) * 10


RESCUE_MARGIN = 10


def owed_by(state: GameState, me: int) -> int:
    return sum(x.amount for x in state.debts if x.debtor == me)


def rescue_shortfall(state: GameState, me: int) -> int:
    owed = owed_by(state, me)
    return max(0, owed - liquidation_cash(state, me)) if owed else 0


def _bank_value(state: GameState, space: int) -> int:
    return state.buildings[space] * (BOARD[space].house_cost // 2) + (0 if state.mortgaged[space] else BOARD[space].mortgage_value)


def liquidation_after(state: GameState, offer: TradeOffer, me: int) -> float:
    _, give_sp, get_sp, give_cash, get_cash, *_ = _sides(offer, me)
    v = liquidation_cash(state, me) - sum(_bank_value(state, s) for s in give_sp) + get_cash - give_cash
    for s in get_sp:
        v += -0.1 * BOARD[s].mortgage_value if state.mortgaged[s] else BOARD[s].mortgage_value
    return v


def rescue_price(state: GameState, me: int, space: int) -> int:
    need = owed_by(state, me) - (liquidation_cash(state, me) - _bank_value(state, space)) + RESCUE_MARGIN
    return max(10, (need + 9) // 10 * 10)


def bid_step(state: GameState, me: int, limit: int, actions, jump: float) -> Action | None:
    a = state.auction
    gap = limit - a.high_bid
    if gap < 1:
        return None
    cap = max(10, jump * max(a.high_bid, BOARD[a.space].price))
    for step in sorted(BID_STEPS, reverse=True):
        if step <= gap and (step <= cap or step == 1):
            bid = Action(ActionKind.BID, amount=step)
            if bid in actions:
                return bid
    return None


@lru_cache(maxsize=None)
def _reach(pos: int, rolls: int) -> tuple[tuple[tuple[int, float], ...], tuple[int, ...]]:
    dice = roll_probs()
    dist = {pos: 1.0}
    total: dict[int, float] = {}
    first: tuple[int, ...] = ()
    for k in range(rolls):
        nxt: dict[int, float] = {}
        for p, pp in dist.items():
            for d, pd in dice.items():
                t = (p + d) % 40
                nxt[t] = nxt.get(t, 0.0) + pp * pd
        for t, pt in nxt.items():
            total[t] = total.get(t, 0.0) + pt
        if k == 0:
            first = tuple(sorted(nxt))
        dist = nxt
    return tuple(sorted(total.items())), first


def rent_danger(state: GameState, me: int, rolls: int = 3) -> tuple[float, float]:
    reach, first = _reach(state.players[me].position, rolls)
    owner, players = state.owner, state.players
    rent: dict[int, float] = {}
    total = 0.0
    for t, pt in reach:
        o = owner[t]
        if o is None or o == me or players[o].bankrupt:
            continue
        r = rent[t] = float(rent_for(state, t, 7))
        total += pt * r
    worst = max((rent.get(t, 0.0) for t in first), default=0.0)
    return total, worst


def build_floor(state: GameState, me: int, share: float) -> int:
    if share <= 0:
        return 0
    exp, worst = rent_danger(state, me)
    return int(share * (exp + 0.5 * worst))
