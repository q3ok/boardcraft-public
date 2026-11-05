"""Priority-based purchase, bidding, trade and development strategies."""
from dataclasses import dataclass, replace

from ..board import BOARD, GROUPS, Kind, RAILROADS, UTILITIES
from ..actions import Action, ActionKind, Decision, Phase
from ..state import GameState
from ..buildings import unmortgage_cost
from ..text import seat_label
from . import Choice, describe_trade, variation
from ..trade import TradeOffer, can_propose, validate_offer
from .safety import feeds_rival
from .trading import trade_score

ROLL = Action(ActionKind.ROLL)
END_TURN = Action(ActionKind.END_TURN)
PASS = Action(ActionKind.PASS)
PASS_BID = Action(ActionKind.PASS_BID)
ACCEPT = Action(ActionKind.ACCEPT_TRADE)
REJECT = Action(ActionKind.REJECT_TRADE)
DECLARE_BANKRUPTCY = Action(ActionKind.DECLARE_BANKRUPTCY)

RAILROAD, UTILITY = "railroad", "utility"

_SEED_SHIFT = 0xB0

# Stable priority for ties between equally attractive deeds.
DEED_ORDER = (29, 37, 16, 24, 20, 25, 35, 15, 5, 12, 30, 2, 17, 33, 11, 34, 39, 4, 38, 0,
              7, 22, 36, 31, 8, 9, 3, 14, 6, 23, 1, 18, 21, 13, 19, 26, 32, 27, 10, 28)
DEED_RANK = {space: rank for rank, space in enumerate(DEED_ORDER)}


@dataclass(frozen=True)
class BonjourParams:
    go_increment: int = 200

    high_priority: tuple[str, ...] = ()
    low_priority: tuple[str, ...] = ()

    trade_variation: float = 0.0
    random_move_probability: float = 0.0


VARIANTS: dict[str, tuple[str, BonjourParams]] = {
    "bonjour-a": ("Bonjour A", BonjourParams()),
    "bonjour-b": ("Bonjour B", BonjourParams(high_priority=(RAILROAD, "dark_blue"), low_priority=(UTILITY,))),
    "bonjour-c": ("Bonjour C", BonjourParams(high_priority=(RAILROAD, "orange", "light_blue"))),
}


@dataclass(frozen=True)
class ResistantBonjourParams(BonjourParams):
    feed_ratio: float = 1.25


RESISTANT: dict[str, tuple[str, ResistantBonjourParams]] = {
    f'resistant-bonjour-{v}': (f'Resistant Bonjour {v.upper()}', ResistantBonjourParams(**vars(VARIANTS[f"bonjour-{v}"][1])))
    for v in "abc"
}


def group_of(space: int) -> str | None:
    kind = BOARD[space].kind
    if kind is Kind.RAILROAD:
        return RAILROAD
    if kind is Kind.UTILITY:
        return UTILITY
    return BOARD[space].group


def _count(state: GameState, player: int, spaces) -> int:
    return sum(1 for i in spaces if state.owner[i] == player)


def completes_set(state: GameState, player: int, space: int) -> bool:
    s = BOARD[space]
    if s.kind is Kind.RAILROAD:
        return _count(state, player, RAILROADS) == 3
    if s.kind is Kind.UTILITY:
        return _count(state, player, UTILITIES) == 1
    if s.group is None:
        return False
    return all(state.owner[i] == player for i in GROUPS[s.group] if i != space)


def is_lone(state: GameState, player: int, space: int) -> bool:
    s = BOARD[space]
    if s.kind is Kind.RAILROAD:
        return _count(state, player, RAILROADS) == 1
    if s.kind is Kind.UTILITY:
        return _count(state, player, UTILITIES) == 1
    return not any(state.owner[i] == player for i in GROUPS[s.group] if i != space)


def full_set(state: GameState, player: int, space: int) -> bool:
    g = BOARD[space].group
    return g is not None and all(state.owner[i] == player for i in GROUPS[g])


def rent_gain(state: GameState, space: int) -> int:
    rents, level = BOARD[space].rents, state.buildings[space]
    return rents[1] - 2 * rents[0] if level == 0 else rents[level + 1] - rents[level]


class BonjourBot:
    VARIANTS = VARIANTS

    def __init__(self, bot_id: str = "bonjour-a"):
        name, params = self.VARIANTS[bot_id]
        self.label = bot_id
        self.name = name
        self.params = params

        self._sent: tuple = ()

        self._variation_rng = variation.new_rng(0)

        self._delegates: list = []

    def reseed(self, seed: int) -> None:
        self._variation_rng = variation.new_rng(seed + _SEED_SHIFT)
        for bot, _ in self._delegates:
            bot.reseed(seed)
        self._sync_delegates()

    def _add_delegate(self, bot):
        self._delegates.append((bot, bot.profile.trade_variation))
        return bot

    def _sync_delegates(self) -> None:
        z = self.params.trade_variation
        for bot, z0 in self._delegates:
            want = z if z > 0 else z0
            if bot.profile.trade_variation != want:
                bot.profile = replace(bot.profile, trade_variation=want)

    def _high(self, space: int) -> bool:
        return group_of(space) in self.params.high_priority

    def _low(self, space: int) -> bool:
        return group_of(space) in self.params.low_priority

    def choose(self, state: GameState, decision: Decision) -> Choice:
        if self._delegates:
            self._sync_delegates()
        eps = self.params.random_move_probability
        if eps > 0 and self._variation_rng.random() < eps:
            a = variation.random_action(state, decision, self._variation_rng, lambda a: self._refused(state, decision, a))
            if a is not None:
                return Choice(a, f'Random move (random move probability {eps:g})')
        return self._decide(state, decision)

    def _refused(self, state: GameState, d: Decision, a: Action) -> bool:
        if a.kind is not ActionKind.ACCEPT_TRADE:
            return False
        return feeds_rival(state, state.trade.offer, d.player, getattr(self.params, "feed_ratio", 1.25))

    def _decide(self, state: GameState, decision: Decision) -> Choice:
        phase = decision.phase
        if phase is Phase.BUY_DECISION:
            return self._buy(state, decision)
        if phase is Phase.AUCTION:
            return self._bid(state, decision)
        if phase is Phase.TRADE_RESPONSE:
            return self._respond(state, decision)
        if phase is Phase.RAISE_FUNDS:
            return self._raise_funds(state, decision)
        if phase is Phase.MORTGAGE_TAKEOVER:
            return self._takeover(state, decision)
        if phase is Phase.TRADE_WINDOW:
            return self._window(state, decision)
        return self._turn(state, decision)

    def _buy(self, state: GameState, d: Decision) -> Choice:
        g = self.params.go_increment
        me = d.player
        space = state.pending_buy
        price = BOARD[space].price
        cash = state.players[me].cash
        buy = Action(ActionKind.BUY)
        completes = completes_set(state, me, space)
        if buy in d.actions:
            need = 2 * g if self._low(space) else g
            if cash - price >= need:
                return Choice(buy, f'Buy, leaving ${cash - price} (threshold ${need})')
            if completes:
                return Choice(buy, 'Buy an affordable property that completes my set')
            if self._high(space):
                return Choice(buy, 'Buy an affordable high-priority property')
        elif completes or self._high(space):
            m = self._mortgage_for(state, me, price, d.actions)
            if m is not None:
                why = 'completes my set' if completes else 'has high priority'
                return Choice(m, f'Mortgage {BOARD[m.space].name} to buy {BOARD[space].name}. Property {why}')
        return Choice(Action(ActionKind.DECLINE), f'Decline. Cash after buying would be ${cash - price}, below ${g}')

    def _mortgage_for(self, state: GameState, me: int, amount: int, actions) -> Action | None:
        best = None
        for a in actions:
            if a.kind is not ActionKind.MORTGAGE:
                continue
            sp = a.space
            if BOARD[sp].mortgage_value < amount or is_lone(state, me, sp) or self._high(sp):
                continue
            if best is None or (BOARD[sp].mortgage_value, sp) < (BOARD[best.space].mortgage_value, best.space):
                best = a
        return best

    def _bid(self, state: GameState, d: Decision) -> Choice:
        a = state.auction
        me = d.player
        price = BOARD[a.space].price
        cash = state.players[me].cash
        cur = a.high_bid
        if self._low(a.space):
            price //= 2
        if cur < price:
            target = cur + (price - cur) / 2
            if target < cash:
                act = self._step(d, target - cur)
                if act is not None:
                    return Choice(act, f'Raise the bid to ${cur + act.amount}, halfway to the price of ${price}')
        elif cur < cash and (completes_set(state, me, a.space) or self._high(a.space)):
            act = self._step(d, (cash - cur) / 4)
            if act is not None:
                why = 'completes my set' if completes_set(state, me, a.space) else 'has high priority'
                return Choice(act, f'Raise the bid to ${cur + act.amount} above the price. The property {why}')
        return Choice(PASS_BID, 'Pass')

    @staticmethod
    def _step(d: Decision, gap: float) -> Action | None:
        best = None
        for a in d.actions:
            if a.kind is ActionKind.BID and a.amount <= gap and (best is None or a.amount > best.amount):
                best = a
        return best

    def _turn(self, state: GameState, d: Decision) -> Choice:
        me = d.player
        g = self.params.go_increment
        if d.phase is Phase.PRE_ROLL:
            if state.players[me].in_jail and state.players[me].cash >= g:
                for a, why in ((Action(ActionKind.USE_JAIL_CARD), 'Use a jail release card'),
                               (Action(ActionKind.PAY_JAIL_FINE), 'Pay the jail fine')):
                    if a in d.actions:
                        return Choice(a, f'{why}. I have at least ${g}')
            c = self._manage(state, d)
            if c is not None:
                return c
            offer = self._next_offer(state, me)
            if offer is not None:
                names = [seat_label(i) for i in range(len(state.players))]
                return Choice(Action(ActionKind.PROPOSE_TRADE, trade=offer), f'Propose: {describe_trade(offer, names)}')
        if ROLL in d.actions:
            return Choice(ROLL, 'Roll the dice')
        if END_TURN in d.actions:
            return Choice(END_TURN, 'End my turn')
        return Choice(d.actions[0], 'Only available move')

    def _window(self, state: GameState, d: Decision) -> Choice:
        if d.player != state.current:
            c = self._manage(state, d)
            if c is not None:
                return Choice(c.action, f'Trade window: {c.explanation}')
        return Choice(PASS, 'Pass. I propose offers only before rolling on my turn')

    def _manage(self, state: GameState, d: Decision) -> Choice | None:
        builds = [a for a in d.actions if a.kind is ActionKind.BUILD
                  and BOARD[a.space].house_cost <= state.players[d.player].cash]
        if builds:
            a = max(builds, key=lambda a: (rent_gain(state, a.space) - BOARD[a.space].house_cost, DEED_RANK[a.space]))
            return Choice(a, f'Build on {BOARD[a.space].name}. Largest rent gain per dollar of construction')
        g = self.params.go_increment
        cash = state.players[d.player].cash
        for a in sorted((a for a in d.actions if a.kind is ActionKind.UNMORTGAGE), key=lambda a: DEED_RANK[a.space]):
            if cash - unmortgage_cost(a.space) >= g:
                return Choice(a, f'Repay the mortgage on {BOARD[a.space].name}. Leaves at least ${g}')
        return None

    def _next_offer(self, state: GameState, me: int) -> TradeOffer | None:
        if not can_propose(state):
            return None
        if not self._sent or self._sent[0] != state.turn:
            self._sent = (state.turn,)
        z = self.params.trade_variation
        ok = []
        for offer in self.offers(state, me):
            if offer.target in self._sent[1:] or validate_offer(state, offer) is not None:
                continue
            ok.append(offer)
            if z <= 0:
                break
        if not ok:
            return None
        offer = ok[variation.pick(self._variation_rng, len(ok), z)]
        self._sent = self._sent + (offer.target,)
        return self._vary(state, offer, me)

    def _vary(self, state: GameState, offer: TradeOffer, me: int) -> TradeOffer:
        z = self.params.trade_variation
        if z <= 0 or not (offer.give_cash or offer.get_cash):
            return offer
        ratio = getattr(self.params, "feed_ratio", 1.25)
        feeds = feeds_rival(state, offer, me, ratio)
        positive = trade_score(state, offer, me) > 0
        left = state.players[me].cash - self.params.go_increment / 2

        def fine(o: TradeOffer) -> bool:
            if o.give_cash > offer.give_cash and o.give_cash >= left:
                return False
            if not feeds and feeds_rival(state, o, me, ratio):
                return False
            return not positive or trade_score(state, o, me) > 0

        return variation.varied(state, self._variation_rng, offer, z, fine)

    def _sales(self, state: GameState, me: int) -> list[tuple[int, int, int]]:
        out = []
        for sp in DEED_ORDER:
            if state.owner[sp] != me or BOARD[sp].kind is not Kind.PROPERTY or state.mortgaged[sp]:
                continue
            if full_set(state, me, sp) or not is_lone(state, me, sp) or self._high(sp):
                continue
            for p, pl in enumerate(state.players):
                if p == me or pl.bankrupt or not completes_set(state, p, sp):
                    continue
                price = BOARD[sp].price * 1.5
                if price < pl.cash / 2:
                    price = pl.cash / 2
                elif price > pl.cash:
                    continue
                out.append((int(price), sp, p))
        out.sort(key=lambda t: -t[0])
        return out

    def _wanted(self, state: GameState, me: int) -> list[tuple[int, int, int]]:
        cash = state.players[me].cash
        out = []
        for sp in range(len(BOARD)):
            p = state.owner[sp]
            if p is None or p == me or state.players[p].bankrupt or state.mortgaged[sp]:
                continue
            high = self._high(sp)
            if not high and BOARD[sp].kind is not Kind.PROPERTY:
                continue
            if full_set(state, p, sp) or not is_lone(state, p, sp):
                continue
            if not (completes_set(state, me, sp) or high):
                continue
            price = BOARD[sp].price * 0.75
            if price > cash:
                continue
            out.append((int(price), sp, p))
        out.sort(key=lambda t: t[0])
        return out

    def offers(self, state: GameState, me: int) -> list[TradeOffer]:
        g = self.params.go_increment
        cash = state.players[me].cash
        sales = self._sales(state, me)

        def sell_offers():
            seen, out = set(), []
            for price, sp, p in sales:
                if p not in seen:
                    seen.add(p)
                    out.append(TradeOffer(me, p, give_spaces=(sp,), get_cash=price))
            return out

        if cash < g:
            return sell_offers()
        wanted = self._wanted(state, me)
        if not wanted:
            return sell_offers()
        out, seen = [], set()
        if sales:
            for rprice, rsp, owner in wanted:
                if owner in seen:
                    continue
                seen.add(owner)
                match = next(((op, osp) for op, osp, p in sales if p == owner), None)
                if match is not None:
                    net = match[0] - rprice
                    out.append(TradeOffer(me, owner, give_spaces=(match[1],), get_spaces=(rsp,),
                                          give_cash=max(0, -net), get_cash=max(0, net)))
        if out:
            return out
        total, seen = 0, set()
        for rprice, rsp, owner in wanted:
            if owner in seen:
                continue
            seen.add(owner)
            pay = int(rprice * 1.5)
            if cash - (total + pay) > g / 2:
                total += pay
                out.append(TradeOffer(me, owner, get_spaces=(rsp,), give_cash=pay))
        return out

    def _respond(self, state: GameState, d: Decision) -> Choice:
        g = self.params.go_increment
        me = d.player
        o = state.trade.offer
        if me == o.target:
            gets, gives, cash_in, cash_out, cards_out = o.give_spaces, o.get_spaces, o.give_cash, o.get_cash, o.get_cards
        else:
            gets, gives, cash_in, cash_out, cards_out = o.get_spaces, o.give_spaces, o.get_cash, o.give_cash, o.give_cards
        cash = state.players[me].cash
        if cards_out:
            return Choice(REJECT, 'Reject. I keep jail release cards out of trades')
        if cash_in <= 0 and not gets:
            return Choice(REJECT, 'Reject. I receive nothing')
        net_out = cash_out - cash_in
        if net_out > cash:
            return Choice(REJECT, 'Reject. I lack the cash')
        if any(state.mortgaged[sp] for sp in (*gets, *gives)):
            return Choice(REJECT, 'Reject. The offer includes a mortgaged property')
        worth = sum(BOARD[sp].price for sp in gets) + cash_in - sum(BOARD[sp].price for sp in gives) - cash_out
        create = sum(1 for sp in gets if completes_set(state, me, sp))
        lose = sum(1 for sp in gives if full_set(state, me, sp))
        if lose > create:
            return Choice(REJECT, 'Reject. I would lose a set')
        if net_out >= cash:
            return Choice(REJECT, 'Reject. The adjustment would use all my cash')
        if create > lose:
            return self._accept(d, 'Accept to complete a set')
        z = self.params.trade_variation
        if z > 0:
            g = g * (1 + self._variation_rng.uniform(-1.0, 1.0) * z * variation.CASH_SHARE)
            bar = variation.threshold(self._variation_rng, o, z)
        else:
            bar = 0
        if cash - net_out < g / 2:
            return Choice(REJECT, f'Reject. I would have less than ${g // 2:.0f}')
        if cash - net_out < g and worth <= bar:
            return Choice(REJECT, f'Reject. I would have less than ${g:.0f}, and the trade yields no gain')
        return self._accept(d, f'Accept. Sets are unchanged and remaining cash is ${cash - net_out}')

    @staticmethod
    def _accept(d: Decision, why: str) -> Choice:
        if ACCEPT in d.actions:
            return Choice(ACCEPT, why)
        return Choice(REJECT, 'Reject. The offer can no longer be accepted')

    def _raise_funds(self, state: GameState, d: Decision) -> Choice:
        me = d.player
        owed = sum(x.amount for x in state.debts if x.debtor == me)
        mortgages = [a for a in d.actions if a.kind is ActionKind.MORTGAGE]
        sells = [a for a in d.actions if a.kind is ActionKind.SELL_BUILDING]
        cheapest = lambda a: (BOARD[a.space].mortgage_value, a.space)
        if mortgages and state.players[me].cash + sum(BOARD[a.space].mortgage_value for a in mortgages) >= owed:
            a = min(mortgages, key=cheapest)
            return Choice(a, f'Mortgage {BOARD[a.space].name}. Mortgages cover the debt and this is the smallest')
        if sells:
            a = min(sells, key=lambda a: (BOARD[a.space].price, a.space))
            return Choice(a, f'Sell a building on {BOARD[a.space].name}. Mortgages are insufficient, sell on the cheapest group')
        if mortgages:
            a = min(mortgages, key=cheapest)
            return Choice(a, f'Mortgage {BOARD[a.space].name}. No other assets remain')
        if DECLARE_BANKRUPTCY in d.actions:
            return Choice(DECLARE_BANKRUPTCY, 'No resources remain to pay the debt. Declare bankruptcy')
        return Choice(d.actions[0], 'Only available move')

    def _takeover(self, state: GameState, d: Decision) -> Choice:
        space = state.pending_takeovers[0][1]
        payoff = Action(ActionKind.UNMORTGAGE, space=space)
        g = self.params.go_increment
        if payoff in d.actions and state.players[d.player].cash - unmortgage_cost(space) >= g:
            return Choice(payoff, f'Repay the mortgage on {BOARD[space].name} now, leaving at least ${g}')
        return Choice(PASS, 'Pay only interest')


class ResistantBonjourBot(BonjourBot):
    VARIANTS = RESISTANT

    def __init__(self, bot_id: str = 'resistant-bonjour-a'):
        super().__init__(bot_id)

    def _respond(self, state: GameState, d: Decision) -> Choice:
        c = super()._respond(state, d)
        if c.action.kind is not ActionKind.ACCEPT_TRADE:
            return c
        offer, me = state.trade.offer, d.player
        if feeds_rival(state, offer, me, self.params.feed_ratio):
            return Choice(REJECT, 'Reject. The price fails to cover the set or second property given to a rival')
        score = trade_score(state, offer, me)
        if score <= 0:
            return Choice(REJECT, f'Reject an unprofitable trade (score {score:.0f})')

        bar = max(0.0, variation.threshold(self._variation_rng, offer, self.params.trade_variation))
        if score <= bar:
            return Choice(REJECT, f"Reject. Score +{score:.0f} below today's threshold of {bar:.0f}")
        return Choice(ACCEPT, f'{c.explanation} (score +{score:.0f})')

    def _refused(self, state: GameState, d: Decision, a: Action) -> bool:
        if a.kind is not ActionKind.ACCEPT_TRADE:
            return False
        return super()._refused(state, d, a) or trade_score(state, state.trade.offer, d.player) <= 0


CALCULATOR_VARIANT_ID = 'bonjour-calculator'
CALCULATOR_VARIANT_NAME = 'Bonjour Calculator'
PURE_ID = 'bonjour-calculator-pure'
PURE_NAME = 'Bonjour Calculator Pure'
AUCTION_CALCULATOR_ID = 'bonjour-calculator-auctions'
AUCTION_CALCULATOR_NAME = 'Bonjour Calculator Auctions'
AUCTION_MIXED_ID = 'bonjour-mixed-auctions'
AUCTION_MIXED_NAME = 'Bonjour Mixed Auctions'

_C = RESISTANT['resistant-bonjour-c'][1]

CALCULATOR_VARIANTS: dict[str, tuple[str, ResistantBonjourParams]] = {
    CALCULATOR_VARIANT_ID: (CALCULATOR_VARIANT_NAME, _C),
    PURE_ID: (PURE_NAME, _C),
}

AUCTIONS: dict[str, tuple[str, ResistantBonjourParams]] = {
    AUCTION_CALCULATOR_ID: (AUCTION_CALCULATOR_NAME, _C),
    AUCTION_MIXED_ID: (AUCTION_MIXED_NAME, _C),
}

PURE_AUCTIONS = frozenset({PURE_ID, AUCTION_CALCULATOR_ID})


def _raise_of(c: Choice) -> int:
    return c.action.amount if c.action.kind is ActionKind.BID else 0


class AuctionBonjourBot(ResistantBonjourBot):
    VARIANTS = AUCTIONS

    def __init__(self, bot_id: str = AUCTION_MIXED_ID):
        super().__init__(bot_id)
        from .calculator import CalculatorBot


        self.calculator = self._add_delegate(CalculatorBot())
        self.pure_auctions = bot_id in PURE_AUCTIONS

    def _bid(self, state: GameState, d: Decision) -> Choice:
        calc = self.calculator._auction(state, d)
        space = state.auction.space
        if self.pure_auctions or not (self._high(space) or completes_set(state, d.player, space)):
            return calc
        bonjour = super()._bid(state, d)
        if _raise_of(bonjour) > _raise_of(calc):
            return Choice(bonjour.action, f'{bonjour.explanation} (Bonjour C bids higher than Calculator)')
        return calc


class CalculatorBonjourBot(AuctionBonjourBot):
    VARIANTS = CALCULATOR_VARIANTS

    def __init__(self, bot_id: str = CALCULATOR_VARIANT_ID):
        super().__init__(bot_id)

    def _next_offer(self, state: GameState, me: int) -> TradeOffer | None:
        return self.calculator._proposal(state, me)

    def _window(self, state: GameState, d: Decision) -> Choice:
        if d.player != state.current:
            c = self._manage(state, d)
            if c is not None:
                return Choice(c.action, f'Trade window: {c.explanation}')
        offer = self.calculator._window_proposal(state, d.player)
        if offer is None:
            return Choice(PASS, 'No offer is worth sending now')
        names = [seat_label(i) for i in range(len(state.players))]
        return Choice(Action(ActionKind.PROPOSE_TRADE, trade=offer), self.calculator._window_reason(state, offer, names))


WINDOWS_B_ID = 'resistant-bonjour-b-plus-windows'
WINDOWS_B_NAME = 'Resistant Bonjour B + Windows'
WINDOWS_C_ID = 'resistant-bonjour-c-plus-windows'
WINDOWS_C_NAME = 'Resistant Bonjour C + Windows'

WINDOWS: dict[str, tuple[str, ResistantBonjourParams]] = {
    WINDOWS_B_ID: (WINDOWS_B_NAME, RESISTANT['resistant-bonjour-b'][1]),
    WINDOWS_C_ID: (WINDOWS_C_NAME, RESISTANT['resistant-bonjour-c'][1]),
}


class WindowBonjourBot(ResistantBonjourBot):
    VARIANTS = WINDOWS

    def __init__(self, bot_id: str = WINDOWS_C_ID):
        super().__init__(bot_id)
        from .wary import WaryBot


        self.wary = self._add_delegate(WaryBot())

    def _window(self, state: GameState, d: Decision) -> Choice:
        if d.player != state.current:
            c = self._manage(state, d)
            if c is not None:
                return Choice(c.action, f'Trade window: {c.explanation}')
        offer = self.wary._window_proposal(state, d.player)
        if offer is None:
            return Choice(PASS, 'No offer is worth sending now')
        names = [seat_label(i) for i in range(len(state.players))]
        return Choice(Action(ActionKind.PROPOSE_TRADE, trade=offer), self.wary._window_reason(state, offer, names))

    def _raise_funds(self, state: GameState, d: Decision) -> Choice:
        sale = self.wary._debtor_sale(state, d)
        return sale if sale is not None else super()._raise_funds(state, d)

    def _respond(self, state: GameState, d: Decision) -> Choice:
        c = super()._respond(state, d)
        if c.action.kind is ActionKind.ACCEPT_TRADE:
            return c
        alt = self.wary._respond(state, d)
        if alt.action.kind is not ActionKind.COUNTER_TRADE:
            return c
        view = replace(state, trade=replace(state.trade, offer=alt.action.trade))
        if super()._respond(view, d).action.kind is not ActionKind.ACCEPT_TRADE:
            return c
        return Choice(alt.action, f'{alt.explanation} (without it: {c.explanation})')
