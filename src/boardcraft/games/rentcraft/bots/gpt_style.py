"""Acquire starting groups, finance three houses and negotiate for missing streets."""
from dataclasses import dataclass

from ..board import BOARD, BUYABLE, GROUPS, HOTEL, Kind
from ..actions import Action, ActionKind, Decision, Phase
from ..state import GameState
from ..buildings import unmortgage_cost
from . import Choice, describe_trade
from ..trade import TradeOffer, validate_offer
from .calculator import CalculatorBot, CalculatorParams, _rivals, income
from .odds import landing, roll_probs
from .profiles import PROFILES
from .rule_bot import END_TURN, ROLL, _completes_group, _in_full_group
from .safety import rent_danger
from .trading import counter_for, proposals

PROFILE = PROFILES['gpt-style']
BASE_RESERVE = 250
MAX_RESERVE = 900
STARTER = ("brown", "light_blue")
NEXT = ("pink", "orange")
PRICE_STEPS = (1.2, 1.5, 1.8, 2.2, 2.6, 3.0)
HORIZON = 25


@dataclass(frozen=True)
class GptStyleParams(CalculatorParams):
    min_reserve: int = BASE_RESERVE
    max_reserve: int = MAX_RESERVE
    worst_share: float = 0.3
    unmortgage_margin: int = 400

    complete_factor: float = 2.5
    block_factor: float = 1.8
    swap_factor: float = 1.5
    starter_factor: float = 1.3
    next_factor: float = 1.3
    rail_factor: float = 1.2
    utility_factor: float = 0.7

    starter_left: int = 100
    threat_left: int = 50

    price_steps: tuple[float, ...] = PRICE_STEPS
    build_money_share: float = 1.5

    horizon: int = HORIZON
    worth_ratio: float = 0.9

    jail_stay: float = 100.0
    jail_stay_strong: float = 40.0


GPT_STYLE_PARAMS = GptStyleParams()


def liquidity(state: GameState, me: int) -> tuple[int, int]:
    cash = state.players[me].cash
    extra = 0
    for i, o in enumerate(state.owner):
        if o != me or state.mortgaged[i]:
            continue
        s = BOARD[i]
        extra += state.buildings[i] * (s.house_cost // 2) + s.mortgage_value
    return cash, cash + extra


def _alive(state: GameState) -> list[int]:
    return [i for i, p in enumerate(state.players) if not p.bankrupt]


def _rival_buildings(state: GameState, me: int) -> bool:
    return any(state.buildings[i] for i, o in enumerate(state.owner) if o is not None and o != me)


def risk(state: GameState, me: int, rolls: int = 3) -> tuple[float, float]:
    return rent_danger(state, me, rolls)


def reserve(state: GameState, me: int, params: CalculatorParams = GPT_STYLE_PARAMS) -> int:
    exp, worst = risk(state, me)
    return int(min(params.max_reserve, max(params.min_reserve, exp + params.worst_share * worst)))


def _rival_threat(state: GameState, me: int, space: int) -> int | None:
    g = BOARD[space].group
    if g is None:
        return None
    others = [state.owner[i] for i in GROUPS[g] if i != space]
    o = others[0]
    if o is None or o == me or any(x != o for x in others) or state.players[o].bankrupt:
        return None
    need = BOARD[space].house_cost * len(GROUPS[g]) * 2
    return o if liquidity(state, o)[1] >= need else None


def _swap_threat(state: GameState, me: int, space: int) -> bool:
    g = BOARD[space].group
    if g is None:
        return False
    owners = {state.owner[i] for i in GROUPS[g]} - {None, me}
    for a in owners:
        missing = [i for i in GROUPS[g] if state.owner[i] != a]
        if len(missing) == 1 and state.owner[missing[0]] not in (None, me, a):
            b = state.owner[missing[0]]
            for g2, sp2 in GROUPS.items():
                miss2 = [i for i in sp2 if state.owner[i] != b]
                if g2 != g and len(miss2) == 1 and state.owner[miss2[0]] == a:
                    return True
    return False


def _group_potential(state: GameState, player: int, owned: set[int], cash: int, horizon: int = HORIZON) -> float:
    v = 0.0
    p = landing()
    rivals = max(1, _rivals(state, player))
    for g, spaces in GROUPS.items():
        if not all(s in owned for s in spaces):
            continue
        built = sum(state.buildings[s] for s in spaces)
        cost3 = BOARD[spaces[0]].house_cost * max(0, 3 * len(spaces) - built)
        share = 1.0 if cost3 == 0 else min(1.0, max(0.0, cash) / cost3)
        v += horizon * rivals * sum(p[s] * BOARD[s].rents[3] for s in spaces) * share
    return v


def trade_value(state: GameState, offer: TradeOffer, me: int, horizon: int = HORIZON) -> tuple[float, float]:
    other = offer.target if me == offer.proposer else offer.proposer
    mine_give, mine_get = (offer.give_spaces, offer.get_spaces) if me == offer.proposer else (offer.get_spaces, offer.give_spaces)
    cash_out, cash_in = (offer.give_cash, offer.get_cash) if me == offer.proposer else (offer.get_cash, offer.give_cash)

    def side(pid: int, gives, gets, pay: int, receive: int) -> float:
        owned = {i for i, o in enumerate(state.owner) if o == pid}
        after = (owned - set(gives)) | set(gets)
        cash_before = state.players[pid].cash

        fees = sum(BOARD[s].mortgage_value * 0.1 for s in gets if state.mortgaged[s])
        later = sum(BOARD[s].mortgage_value * 1.1 for s in gets if state.mortgaged[s]) * 0.5
        cash_after = cash_before - pay + receive - fees
        before = _group_potential(state, pid, owned, cash_before, horizon) + sum(BOARD[s].price for s in owned)
        after_v = _group_potential(state, pid, after, int(cash_after), horizon) + sum(BOARD[s].price for s in after)
        return after_v - before + receive - pay - fees - later

    mine = side(me, mine_give, mine_get, cash_out, cash_in)
    theirs = side(other, mine_get, mine_give, cash_in, cash_out)
    return mine, theirs


class GptStyleBot(CalculatorBot):
    def __init__(self):
        super().__init__()
        self.profile = PROFILE
        self.label = PROFILE.id
        self.params = GPT_STYLE_PARAMS
        self._attempts: dict[tuple[int, int], int] = {}
        self._auction_limit: dict[int, tuple[int, str]] = {}

    def _reserve(self, state: GameState, me: int) -> int:
        return reserve(state, me, self.params)

    def _decide(self, state: GameState, decision: Decision) -> Choice:
        if decision.phase is not Phase.AUCTION:
            self._auction_limit.clear()
        try:
            return super()._decide(state, decision)
        except Exception:
            return Choice(self._fallback(state, decision), "Use the available fallback move.")

    @staticmethod
    def _fallback(state: GameState, d: Decision) -> Action:
        acts = d.actions
        for kind in (ActionKind.MORTGAGE, ActionKind.SELL_BUILDING) if d.phase is Phase.RAISE_FUNDS else ():
            for a in acts:
                if a.kind is kind:
                    return a
        for a in (Action(ActionKind.REJECT_TRADE), Action(ActionKind.PASS_BID), Action(ActionKind.PASS), ROLL, END_TURN):
            if a in acts:
                return a
        return acts[0]

    def _buy(self, state: GameState, d: Decision) -> Choice:
        me = d.player
        space = state.pending_buy
        s = BOARD[space]
        cash, liquid = liquidity(state, me)
        left = cash - s.price
        res = self._reserve(state, me)
        early = sum(1 for i in BUYABLE if state.owner[i] is None) >= 8
        buy, decline = Action(ActionKind.BUY), Action(ActionKind.DECLINE)
        if buy not in d.actions:
            return Choice(decline, 'Decline. I cannot afford it')
        if _completes_group(state, me, space) and liquid - s.price >= res // 2:
            return Choice(buy, f'Buy to complete a group worth several times the price, leaving ${left}')
        threat = _rival_threat(state, me, space)
        if threat is not None and left >= self.params.threat_left:
            return Choice(buy, f'Buy to block the final street for seat [{threat}], who can finance construction')
        if early and s.group in STARTER and left >= self.params.starter_left:
            return Choice(buy, f'Buy a cheap starting group for an early set and affordable houses, leaving ${left}')
        if s.kind is Kind.UTILITY and not early:
            return Choice(decline, 'Decline. The utility earns too little to justify using cash')
        need = res + (s.price // 2 if s.group in ("green", "dark_blue", "yellow", "red") and not early else 0)
        if left >= need:
            return Choice(buy, f'Buy, leaving ${left}, reserve ${res} preserved')
        return Choice(decline, f'Decline. Remaining cash would be ${left}, and my reserve for the next moves is ${need}')

    def _value(self, state: GameState, me: int, space: int) -> tuple[float, str]:
        s = BOARD[space]
        prm = self.params
        early = sum(1 for i in BUYABLE if state.owner[i] is None) >= 8
        if _completes_group(state, me, space):
            return s.price * prm.complete_factor, 'completes my color group'
        if _rival_threat(state, me, space) is not None:
            return s.price * prm.block_factor, 'blocks a well-funded rival'
        if _swap_threat(state, me, space):
            return s.price * prm.swap_factor, 'two rivals could complete their groups with one trade'
        if s.group in STARTER and early:
            return s.price * prm.starter_factor, 'cheap starting group'
        if s.group in NEXT:
            return s.price * prm.next_factor, 'good next target'
        if s.kind is Kind.RAILROAD:
            return s.price * prm.rail_factor, 'station'
        if s.kind is Kind.UTILITY:
            return s.price * prm.utility_factor, 'utility income is low'
        return s.price * 1.0, 'ordinary property'

    def _auction(self, state: GameState, d: Decision) -> Choice:
        me = d.player
        a = state.auction
        if a.space not in self._auction_limit:
            value, why = self._value(state, me, a.space)
            cap = self._auction_cash(state, me) - self._reserve(state, me) // 2
            self._auction_limit[a.space] = (int(max(0, min(value, cap))), why)
        limit, why = self._auction_limit[a.space]

        others = [liquidity(state, b)[1] for b in a.bidders if b != me]
        if others and max(others) < limit:
            limit = max(others) + 1
            why += ', rivals cannot raise more'
        bid = self._bid(state, d, limit)
        if bid is not None:
            return Choice(bid, f'Raise the bid to ${a.high_bid + bid.amount}: {why} (limit ${limit})')
        return Choice(Action(ActionKind.PASS_BID), f'Pass. Above limit ${limit} ({why})')

    def _jail(self, state: GameState, d: Decision) -> Choice | None:
        me = d.player
        unowned = sum(1 for i in BUYABLE if state.owner[i] is None)
        exp, _ = risk(state, me, rolls=1)
        mine_strong = any(state.buildings[i] >= 3 for i, o in enumerate(state.owner) if o == me)
        if unowned >= self.params.jail_unowned and exp < self.params.jail_exp:
            return super()._jail(state, d)
        if (mine_strong and exp > self.params.jail_stay_strong) or exp > self.params.jail_stay:
            return Choice(ROLL, f'Stay in jail. My properties earn rent and leaving risks expected rent of ${exp:.0f}')
        return super()._jail(state, d)

    def _urgency(self, state: GameState, me: int, space: int) -> float:
        dice = roll_probs()
        u = 0.0
        for i, p in enumerate(state.players):
            if i != me and not p.bankrupt:
                u += dice.get((space - p.position) % 40, 0.0)
        return u

    def _group_roi3(self, state: GameState, me: int, group: str) -> float:
        spaces = GROUPS[group]
        gain = sum(income(state, me, s, 3) - income(state, me, s) for s in spaces)
        cost = BOARD[spaces[0]].house_cost * sum(max(0, 3 - state.buildings[s]) for s in spaces)
        return gain / cost if cost else 0.0

    def _manage(self, state: GameState, d: Decision) -> Choice | None:
        me = d.player
        cash = state.players[me].cash
        acts = d.actions

        res = max(self._reserve(state, me), self._build_floor(state, me, acts))
        builds = [a for a in acts if a.kind is ActionKind.BUILD]
        rival_needs = any(
            all(state.owner[i] == o for i in g) and max(state.buildings[i] for i in g) < 3
            for g in GROUPS.values() for o in {state.owner[g[0]]} if o is not None and o != me
        )
        if state.bank_houses <= 6 and rival_needs:
            builds = [a for a in builds if state.buildings[a.space] + 1 < HOTEL]
        below3 = [a for a in builds if state.buildings[a.space] < 3]
        if below3:
            best_group = max({BOARD[a.space].group for a in below3}, key=lambda g: self._group_roi3(state, me, g))
            cands = [a for a in below3 if BOARD[a.space].group == best_group]
            a = max(cands, key=lambda a: self._urgency(state, me, a.space))
            cost = BOARD[a.space].house_cost
            if cash - cost >= res:
                return Choice(a, f'Build {state.buildings[a.space] + 1} houses on {BOARD[a.space].name}. Build the best group to three houses for the jump in rent')
            m = self._mortgage_for(state, me, acts, cost, res, self._build_gain(state, me, a) * cost)
            if m is not None:
                return m
        elif builds:
            a = max(builds, key=lambda a: self._build_gain(state, me, a) * (1 + 3 * self._urgency(state, me, a.space)))
            cost = BOARD[a.space].house_cost
            if cash - cost >= res:
                lvl = state.buildings[a.space] + 1
                what = "hotel" if lvl == HOTEL else f'{lvl}houses'
                return Choice(a, f'Build {what} on {BOARD[a.space].name}. Best income increase per dollar')

        endgame = len(_alive(state)) == 2
        for a in acts:
            if a.kind is ActionKind.UNMORTGAGE and BOARD[a.space].kind is Kind.PROPERTY:
                cost = unmortgage_cost(a.space)
                if endgame and cash - cost >= res:
                    return Choice(a, f'Repay the mortgage on {BOARD[a.space].name}. Endgame rent opportunity against the last rival')
                if _in_full_group(state, me, a.space) and not builds and cash - cost >= res + self.params.unmortgage_margin:
                    return Choice(a, f'Repay the mortgage on {BOARD[a.space].name}. Restores my set')
        return None

    def _mortgage_for(self, state: GameState, me: int, acts, cost: int, res: int, gain: float) -> Choice | None:
        loose = [a for a in acts if a.kind is ActionKind.MORTGAGE and not _in_full_group(state, me, a.space)]
        if not loose:
            return None
        m = min(loose, key=lambda a: income(state, me, a.space) / BOARD[a.space].mortgage_value)
        lost = income(state, me, m.space)
        if state.players[me].cash + BOARD[m.space].mortgage_value - cost >= res and gain > self.params.mortgage_gain_ratio * lost:
            return Choice(m, f'Mortgage {BOARD[m.space].name} to build. The house adds ${gain:.1f} per roll, the property loses ${lost:.1f}')
        return None

    def _raise_funds(self, state: GameState, d: Decision) -> Choice:
        me = d.player
        acts = d.actions
        loose = [a for a in acts if a.kind is ActionKind.MORTGAGE and not _in_full_group(state, me, a.space)]
        if loose:
            a = min(loose, key=lambda a: income(state, me, a.space) / BOARD[a.space].mortgage_value)
            return Choice(a, f'Mortgage {BOARD[a.space].name}. Single property outside my main income source')
        sells = [a for a in acts if a.kind is ActionKind.SELL_BUILDING]
        if sells:
            def loss(a: Action) -> float:
                if a.space is None:
                    return 1e9
                lvl = state.buildings[a.space]
                per = (income(state, me, a.space, lvl) - income(state, me, a.space, lvl - 1)) / max(1, BOARD[a.space].house_cost // 2)
                return per * (3.0 if lvl == 3 else 1.0)
            a = min(sells, key=loss)
            where = BOARD[a.space].name if a.space is not None else 'entire group'
            return Choice(a, f'Sell a building on {where}. Smallest rent loss per dollar raised')
        mortgages = [a for a in acts if a.kind is ActionKind.MORTGAGE]
        if mortgages:
            a = min(mortgages, key=lambda a: income(state, me, a.space) / BOARD[a.space].mortgage_value)
            return Choice(a, f'Mortgage {BOARD[a.space].name}. No other assets can be liquidated')
        return super()._raise_funds(state, d)

    def _candidates(self, state: GameState, pid: int) -> list:
        out = []
        cash = state.players[pid].cash
        res = None
        for g, spaces in GROUPS.items():
            missing = [s for s in spaces if state.owner[s] != pid]
            if len(missing) != 1:
                continue
            m = missing[0]
            x = state.owner[m]
            if x is None or state.players[x].bankrupt or state.buildings[m]:
                continue
            if res is None:
                res = self._reserve(state, pid)
            build_money = int(BOARD[m].house_cost * len(spaces) * self.params.build_money_share)
            cap = cash - res - build_money
            k = self._attempts.get((x, m), 0)
            if k >= len(self.params.price_steps):
                continue
            offer_cash = int(BOARD[m].price * self.params.price_steps[k]) // 10 * 10
            if offer_cash > cap:
                continue
            offer = TradeOffer(pid, x, give_cash=offer_cash, get_spaces=(m,))
            if validate_offer(state, offer) is None:
                out.append((-self._group_roi3(state, pid, g), offer))
        out.sort(key=lambda t: t[0])
        swaps = [o for o in proposals(state, pid, self.params.min_reserve) if o.give_spaces]
        return [o for _, o in out] + swaps

    def _on_proposed(self, offer) -> None:
        if offer.get_spaces and not offer.give_spaces:
            key = (offer.target, offer.get_spaces[0])
            self._attempts[key] = self._attempts.get(key, 0) + 1

    def _proposal_reason(self, state: GameState, offer, names: list[str]) -> str:
        if offer.give_cash and not offer.give_spaces:
            k = self._attempts.get((offer.target, offer.get_spaces[0]), 1)
            return f'Negotiate for the missing street (attempt {k}, premium increases after refusal): {describe_trade(offer, names)}'
        return f'Propose a trade that allows both sides to build: {describe_trade(offer, names)}'

    def _worth_it(self, mine: float, theirs: float, shift: float = 0.0) -> bool:
        return mine - self.params.worth_ratio * theirs > shift

    def _respond(self, state: GameState, d: Decision) -> Choice:
        feed = self._feed_response(state, d)
        if feed is not None:
            return feed
        offer = state.trade.offer
        me = d.player
        mine, theirs = trade_value(state, offer, me, self.params.horizon)
        pay = offer.get_cash if me == offer.target else offer.give_cash
        cash_after = state.players[me].cash - pay
        if self._worth_it(mine, theirs, self._accept_shift(offer)) and cash_after >= self._reserve(state, me) // 2:
            return Choice(Action(ActionKind.ACCEPT_TRADE), f'Accept. My gain after trading is {mine:.0f}, rival {theirs:.0f}')
        if state.trade.exchanges < state.config.max_trade_exchanges:
            counter = counter_for(state, offer, me)
            if counter is not None:
                cm, ct = trade_value(state, counter, me, self.params.horizon)
                if self._worth_it(cm, ct):
                    cash = state.players[offer.proposer].cash
                    counter = self._vary(state, counter, me, lambda o: o.get_cash <= cash
                                         and self._worth_it(*trade_value(state, o, me, self.params.horizon)))
                    return Choice(Action(ActionKind.COUNTER_TRADE, trade=counter), 'With a cash adjustment the trade is profitable')
        return Choice(Action(ActionKind.REJECT_TRADE), f'Reject. My gain would be {mine:.0f}, rival {theirs:.0f}, keeping a dangerous set out of a cash-only trade')
