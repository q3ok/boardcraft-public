"""Block rival sets and use public negotiation history to price future offers."""
from dataclasses import dataclass

from ..board import BOARD, GROUPS, Kind, RAILROADS, UTILITIES
from ..actions import Action, ActionKind, Decision
from ..state import GameState
from ..text import seat_label
from ..trade import TradeOffer, validate_offer
from . import Choice
from .bonjour import (
    ACCEPT, WINDOWS, WINDOWS_C_ID, PASS, PASS_BID, REJECT, ResistantBonjourBot, ResistantBonjourParams, WindowBonjourBot,
    completes_set,
)
from .safety import FEED_SET_BONUS, NEAR_SHARE, bid_step, rescue_shortfall
from .trading import RIVAL_WEIGHT, holdings_value, liquidation_cash, raise_mortgage, trade_score


CONTROLLER_ID = 'controller'
CONTROLLER_NAME = 'Controller'


WILLING, UNWILLING = 'willing', 'unwilling'

BID_JUMP = 0.25

PRICE_STEPS = (1.0, 1.25, 1.5, 2.0, 3.0)
_GROUP_PRICE = {g: sum(BOARD[s].price for s in spaces) for g, spaces in GROUPS.items()}


@dataclass(frozen=True)
class ControllerParams(ResistantBonjourParams):
    blocking_limit: float = 0.0
    blocking_limit_with_set: float = 2.0

    willing_seller_threshold: float = 0.05
    neutral_seller_threshold: float = 0.15
    reluctant_seller_threshold: float = 0.5

    repeat_cash_change: float = 100.0


CONTROLLER: dict[str, tuple[str, ControllerParams]] = {
    CONTROLLER_ID: (CONTROLLER_NAME, ControllerParams(**vars(WINDOWS[WINDOWS_C_ID][1]))),
}


def same_kind(space: int) -> tuple[int, ...]:
    s = BOARD[space]
    if s.kind is Kind.RAILROAD:
        return RAILROADS
    if s.kind is Kind.UTILITY:
        return UTILITIES
    return GROUPS[s.group] if s.group else (space,)


def closes_for(state: GameState, me: int, space: int) -> int | None:
    for p, pl in enumerate(state.players):
        if p != me and not pl.bankrupt and completes_set(state, p, space):
            return p
    return None


def has_full_set(state: GameState, me: int) -> bool:
    return any(all(state.owner[i] == me for i in spaces) for spaces in GROUPS.values())


def _sides(offer: TradeOffer, me: int):
    if me == offer.proposer:
        return offer.target, offer.give_spaces, offer.get_spaces, offer.give_cash, offer.give_cards
    return offer.proposer, offer.get_spaces, offer.give_spaces, offer.get_cash, offer.get_cards


def gives_last(state: GameState, offer: TradeOffer, me: int) -> int | None:
    _, give_sp, get_sp, _, _ = _sides(offer, me)
    for sp in give_sp:
        kind = same_kind(sp)
        before = sum(1 for i in kind if state.owner[i] == me)
        after = sum(1 for i in kind if (state.owner[i] == me and i not in give_sp) or i in get_sp)
        if before and not after:
            return sp
    return None


def given_value(state: GameState, offer: TradeOffer, me: int) -> float:
    _, give_sp, _, give_cash, give_cards = _sides(offer, me)
    mine = {i for i, o in enumerate(state.owner) if o == me}
    return holdings_value(mine) - holdings_value(mine - set(give_sp)) + give_cash + 50 * give_cards


def can_collect(state: GameState, me: int, group: str) -> bool:
    holders = {state.owner[s] for s in GROUPS[group] if state.owner[s] not in (None, me)}
    return len(holders) <= 1


def block_premium(state: GameState, offer: TradeOffer, me: int) -> float:
    other, _, get_sp, _, _ = _sides(offer, me)
    v = 0.0
    for g in {BOARD[s].group for s in get_sp if BOARD[s].group}:
        spaces = GROUPS[g]
        if can_collect(state, me, g):
            continue
        n0 = sum(1 for s in spaces if state.owner[s] == other)
        n1 = n0 - sum(1 for s in get_sp if s in spaces and state.owner[s] == other)
        if n0 >= len(spaces) - 1 > n1:
            v += NEAR_SHARE * FEED_SET_BONUS * _GROUP_PRICE[g]
    return v


def _is_buy(e) -> bool:
    return bool(e.get) and not e.give and not e.truncated


def journal(state: GameState, me: int) -> dict[int, str]:
    m = state.memory
    out: dict[int, str] = {}
    for e in (m.log if m is not None else ()):
        kind = e.kind
        if kind in ("trade_offer", "trade_counter"):
            if e.to == me and e.who != me and e.give:
                out[e.who] = WILLING
        elif kind == "trade":
            if e.who == me and e.to is not None and _is_buy(e):
                out[e.to] = WILLING
        elif kind == "trade_reject":
            if e.to == me and e.who is not None and _is_buy(e) and e.amount >= sum(BOARD[s].price for s in e.get):
                out[e.who] = UNWILLING
    return out


def rejected_offer(state: GameState, offer: TradeOffer, me: int, min_cash: float) -> bool:
    m = state.memory
    if m is None or me != offer.proposer or not m.pair_offers:
        return False
    key = (frozenset(offer.give_spaces), frozenset(offer.get_spaces), offer.give_cash - offer.get_cash,
           (offer.give_cards, offer.get_cards))
    hist = m.pair_offers[me][offer.target]
    last = next((i for i in range(len(hist) - 1, -1, -1)
                 if (frozenset(hist[i].give), frozenset(hist[i].get), hist[i].cash, hist[i].cards) == key), None)
    if last is None:
        return False
    old = hist[last]
    if old.outcome == 'accepted' or old.ctx is None:
        return False
    if old.outcome == 'pending' and state.trade is not None and last == len(hist) - 1:
        return False
    cash, mine, theirs = old.ctx
    t = offer.target
    if (abs(state.players[t].cash - cash) >= min_cash
            or tuple(i for i, w in enumerate(state.owner) if w == me) != mine
            or tuple(i for i, w in enumerate(state.owner) if w == t) != theirs
            or any(d.debtor == t for d in state.debts)):
        return False
    return True


class ControllerBot(WindowBonjourBot):
    VARIANTS = CONTROLLER

    def __init__(self, bot_id: str = CONTROLLER_ID):
        super().__init__(bot_id)

        self._why: tuple = ()

        self._blocked: list[str] = []

    def _bar(self, state: GameState, me: int, partner: int) -> tuple[float, str]:
        p = self.params
        mark = journal(state, me).get(partner)
        who = seat_label(partner)
        if mark == WILLING:
            bar, why = p.willing_seller_threshold, f'{who} wanted to sell to me'
        elif mark == UNWILLING:
            bar, why = p.reluctant_seller_threshold, f'{who} refused to sell to me'
        else:
            bar, why = p.neutral_seller_threshold, f'{who} has no recent sale history'
        return bar, f'{why}, required gain +{bar * 100:g}%'

    def _refusal(self, state: GameState, offer: TradeOffer, me: int, score: float) -> str | None:
        sp = gives_last(state, offer, me)
        if sp is not None:
            return f'{BOARD[sp].name} is my last property of this color. I will keep it'
        bar, rule = self._bar(state, me, _sides(offer, me)[0])
        if score <= 0 or score < bar * given_value(state, offer, me):
            return rule
        return None

    def _own_score(self, state: GameState, offer: TradeOffer, me: int) -> float:
        return trade_score(state, offer, me) + RIVAL_WEIGHT * block_premium(state, offer, me)

    def _own_rules(self, state: GameState, offer: TradeOffer, me: int) -> str | None:
        if rescue_shortfall(state, me) > 0:
            return None
        return self._refusal(state, offer, me, self._own_score(state, offer, me))

    def _repeat(self, state: GameState, offer: TradeOffer, me: int) -> str | None:
        if rescue_shortfall(state, me) > 0 or not rejected_offer(state, offer, me, self.params.repeat_cash_change):
            return None
        why = f'Avoid repeating an offer rejected by seat [{offer.target}]'
        if why not in self._blocked:
            self._blocked.append(why)
        return why

    def _own_refusal(self, state: GameState, offer: TradeOffer, me: int) -> str | None:
        return self._own_rules(state, offer, me) or self._repeat(state, offer, me)

    def _block_reason(self, state: GameState, me: int, space: int) -> str | None:
        rival = closes_for(state, me, space)
        if rival is not None:
            return f'property completes a set for {seat_label(rival)}, blocking the deal'
        if BOARD[space].kind is Kind.RAILROAD:
            return 'I buy stations whenever I can afford them'
        return None

    def _buy(self, state: GameState, d: Decision) -> Choice:
        me, space = d.player, state.pending_buy
        why = self._block_reason(state, me, space)
        if why is not None:
            name = BOARD[space].name
            buy = Action(ActionKind.BUY)
            if buy in d.actions:
                return Choice(buy, f'Buy {name}: {why}')
            m = raise_mortgage(state, me, BOARD[space].price, space, d.actions)
            if m is not None:
                return Choice(m, f'Mortgage {BOARD[m.space].name} to buy {name}: {why}')
        return super()._buy(state, d)

    def _block_limit(self, state: GameState, me: int, space: int) -> int:
        cap = liquidation_cash(state, me)
        p = self.params
        factor = p.blocking_limit_with_set if has_full_set(state, me) else p.blocking_limit
        return cap if factor <= 0 else min(cap, int(factor * BOARD[space].price))

    def _bid(self, state: GameState, d: Decision) -> Choice:
        a = state.auction
        me = d.player
        why = self._block_reason(state, me, a.space)
        if why is None:
            return super()._bid(state, d)
        limit = self._block_limit(state, me, a.space)
        act = bid_step(state, me, limit, d.actions, BID_JUMP)
        if act is not None:
            return Choice(act, f'Raise the bid to ${a.high_bid + act.amount}: {why} (limit ${limit})')
        return Choice(PASS_BID, f'Pass. {why}, but the price reached my limit of ${limit}')

    def _respond(self, state: GameState, d: Decision) -> Choice:
        me = d.player
        c = super()._respond(state, d)
        if rescue_shortfall(state, me) > 0:
            return c
        if c.action.kind is ActionKind.ACCEPT_TRADE:
            offer = state.trade.offer
            score = trade_score(state, offer, me)
            why = self._refusal(state, offer, me, score)
            if why is not None:
                return Choice(REJECT, f'Reject. {why}')
            given = given_value(state, offer, me)
            gain = f'gain +{score / given * 100:.0f}%' if given > 0 else 'I give up nothing'
            return Choice(ACCEPT, f"{c.explanation}; {self._bar(state, me, offer.proposer)[1]}, {gain}")
        if c.action.kind is ActionKind.COUNTER_TRADE:
            self._blocked = []
            why = self._own_refusal(state, c.action.trade, me)
            if why is not None:
                return Choice(REJECT, f'Reject without a counteroffer. {why}')
        return c

    def _refused(self, state: GameState, d: Decision, a: Action) -> bool:
        if super()._refused(state, d, a):
            return True
        if a.kind is not ActionKind.ACCEPT_TRADE or rescue_shortfall(state, d.player) > 0:
            return False
        offer = state.trade.offer
        return self._refusal(state, offer, d.player, trade_score(state, offer, d.player)) is not None

    def _priced(self, state: GameState, me: int, seller: int, spaces: tuple[int, ...]) -> TradeOffer | None:
        cash = state.players[me].cash
        total = sum(BOARD[s].price for s in spaces)
        for mult in PRICE_STEPS:
            c = max(10, int(total * mult) // 10 * 10)
            if cash - c <= self.params.go_increment / 2:
                break
            offer = TradeOffer(me, seller, get_spaces=spaces, give_cash=c)
            if validate_offer(state, offer) is not None or trade_score(state, offer, seller) <= 0:
                continue
            if self._own_rules(state, offer, me) is not None:
                break
            if self._repeat(state, offer, me) is not None:
                continue
            return offer
        return None

    def _goal_offers(self, state: GameState, me: int) -> list[tuple[TradeOffer, str]]:
        out = []
        for g, spaces in GROUPS.items():
            rest = [s for s in spaces if state.owner[s] != me]
            if not rest:
                continue
            holders = sorted({state.owner[s] for s in rest if state.owner[s] is not None})
            if len(holders) <= 1:
                x = holders[0] if holders else None
                if (x is None or len(rest) == len(spaces) or any(state.owner[s] is None for s in rest)
                        or state.players[x].bankrupt):
                    continue
                offer = self._priced(state, me, x, tuple(rest))
                if offer is not None:
                    out.append((offer, 'Buy missing properties to complete my set'))
                continue
            for x in holders:
                theirs = [s for s in spaces if state.owner[s] == x]
                if len(theirs) != len(spaces) - 1 or state.players[x].bankrupt:
                    continue
                sp = min(theirs, key=lambda s: (BOARD[s].price, s))
                offer = self._priced(state, me, x, (sp,))
                if offer is not None:
                    out.append((offer, f'Deny an opponent the last street in a group I cannot complete. Take from {seat_label(x)}'))
        return out

    def offers(self, state: GameState, me: int) -> list[TradeOffer]:
        goal = self._goal_offers(state, me)
        self._why = tuple(goal)
        base = [o for o in super().offers(state, me) if self._own_refusal(state, o, me) is None]
        return [o for o, _ in goal] + base

    def _turn(self, state: GameState, d: Decision) -> Choice:
        self._why = ()
        self._blocked = []
        c = super()._turn(state, d)
        if c.action.kind is ActionKind.PROPOSE_TRADE:
            why = dict(self._why).get(c.action.trade)
            if why is not None:
                return Choice(c.action, f'{why}. {c.explanation}')
        elif self._blocked:
            return Choice(c.action, f"{c.explanation} ({'; '.join(self._blocked)})")
        return c

    def _window(self, state: GameState, d: Decision) -> Choice:
        if d.player != state.current:
            c = self._manage(state, d)
            if c is not None:
                return Choice(c.action, f'Trade window: {c.explanation}')
        self._blocked = []
        offer = self.wary._window_proposal(state, d.player)
        if offer is None or self._own_refusal(state, offer, d.player) is not None:
            return Choice(PASS, "; ".join(['No offer is worth sending now', *self._blocked]))
        names = [seat_label(i) for i in range(len(state.players))]
        return Choice(Action(ActionKind.PROPOSE_TRADE, trade=offer), self.wary._window_reason(state, offer, names))

    def _raise_funds(self, state: GameState, d: Decision) -> Choice:
        self._blocked = []
        sale = self.wary._debtor_sale(state, d)
        if sale is not None and self._own_refusal(state, sale.action.trade, d.player) is None:
            return sale
        c = ResistantBonjourBot._raise_funds(self, state, d)
        return Choice(c.action, f"{c.explanation} ({'; '.join(self._blocked)})") if self._blocked else c
