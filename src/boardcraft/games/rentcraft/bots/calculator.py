"""Value properties from landing probabilities, expected rent and available cash."""
from dataclasses import dataclass

from ..board import BOARD, BUYABLE, GROUPS, HOTEL, Kind, RAILROADS, UTILITIES
from ..actions import Action, ActionKind, Decision
from ..state import GameState
from ..buildings import unmortgage_cost
from ..rent import rent_for
from . import Choice
from .odds import landing, roll_probs
from .profiles import PROFILES
from .rule_bot import ROLL, RuleBot, _completes_group, _early_game, _in_full_group
from .trading import proposals

PROFILE = PROFILES['calculator']
MIN_RESERVE = 100
MAX_RESERVE = 800

_AVG_DICE = 7


@dataclass(frozen=True)
class CalculatorParams:
    min_reserve: int = MIN_RESERVE
    max_reserve: int = MAX_RESERVE
    worst_share: float = 0.25

    complete_factor: float = 2.0
    block_factor: float = 1.5
    rail_factor: float = 1.3
    utility_factor: float = 0.7

    mortgage_gain_ratio: float = 2.0

    unmortgage_margin: int = 500

    jail_unowned: int = 6
    jail_exp: float = 60.0


CALCULATOR_PARAMS = CalculatorParams()


def _rent_at(state: GameState, space: int, level: int | None = None, owner: int | None = None) -> float:
    s = BOARD[space]
    owner = state.owner[space] if owner is None else owner
    if owner is None:
        return 0.0
    if s.kind is Kind.PROPERTY:
        lvl = state.buildings[space] if level is None else level
        if lvl > 0:
            return float(s.rents[lvl])
        full = all(state.owner[i] == owner for i in GROUPS[s.group])
        return float(s.rents[0] * (2 if full else 1))
    if s.kind is Kind.RAILROAD:
        n = sum(1 for i in RAILROADS if state.owner[i] == owner)
        return float((25, 50, 100, 200)[max(1, n) - 1])
    if s.kind is Kind.UTILITY:
        n = sum(1 for i in UTILITIES if state.owner[i] == owner)
        return float((4, 10)[max(1, n) - 1] * _AVG_DICE)
    return 0.0


def _rivals(state: GameState, me: int) -> int:
    return sum(1 for i, p in enumerate(state.players) if i != me and not p.bankrupt)


def income(state: GameState, me: int, space: int, level: int | None = None) -> float:
    if state.mortgaged[space]:
        return 0.0
    return landing()[space] * _rent_at(state, space, level, owner=me) * _rivals(state, me)


def exposure(state: GameState, me: int, pos: int) -> tuple[float, float]:
    exp = worst = 0.0
    for d, pd in roll_probs().items():
        t = (pos + d) % 40
        o = state.owner[t]
        if o is None or o == me or state.players[o].bankrupt:
            continue
        r = float(rent_for(state, t, d))
        exp += pd * r
        worst = max(worst, r)
    return exp, worst


def reserve(state: GameState, me: int, params: CalculatorParams = CALCULATOR_PARAMS) -> int:
    exp, worst = exposure(state, me, state.players[me].position)
    return int(min(params.max_reserve, max(params.min_reserve, exp + params.worst_share * worst)))


def _blocks_rival(state: GameState, me: int, space: int) -> bool:
    group = BOARD[space].group
    if group is None:
        return False
    others = [state.owner[i] for i in GROUPS[group] if i != space]
    return others[0] is not None and others[0] != me and all(o == others[0] for o in others)


class CalculatorBot(RuleBot):
    def __init__(self):
        super().__init__(PROFILE)
        self.params = CALCULATOR_PARAMS

    def _reserve(self, state: GameState, me: int) -> int:
        return reserve(state, me, self.params)

    def _buy(self, state: GameState, d: Decision) -> Choice:
        me = d.player
        space = state.pending_buy
        s = BOARD[space]
        left = state.players[me].cash - s.price
        res = self._reserve(state, me)
        buy, decline = Action(ActionKind.BUY), Action(ActionKind.DECLINE)
        if buy not in d.actions:
            return Choice(decline, 'Decline. I cannot afford it')
        if (_completes_group(state, me, space) or _blocks_rival(state, me, space)) and left >= self.params.min_reserve:
            why = 'completes my set' if _completes_group(state, me, space) else "blocks a rival's set"
            return Choice(buy, f'Buy: {why}, leaving ${left}')
        if s.kind is Kind.UTILITY and not _early_game(state):
            return Choice(decline, f'Decline. The utility earns about ${landing()[space] * 4 * _AVG_DICE:.1f} per rival roll is too little')
        if left >= res:
            return Choice(buy, f'Buy, leaving ${left}, and my reserve for the next roll is ${res}')
        return Choice(decline, f'Decline. Remaining cash would be ${left}, below my reserve of ${res} (rent exposure on the next roll)')

    def _limit(self, state: GameState, me: int, space: int) -> tuple[int, str]:
        s = BOARD[space]
        prm = self.params
        p = landing()
        props = [i for i in BUYABLE if BOARD[i].kind is Kind.PROPERTY]
        rel = (p[space] / (sum(p[i] for i in props) / len(props))) ** 0.5
        if _completes_group(state, me, space):
            f, why = prm.complete_factor, 'completes my set'
        elif _blocks_rival(state, me, space):
            f, why = prm.block_factor, "blocks a rival's set"
        elif s.kind is Kind.RAILROAD:
            f, why = prm.rail_factor, f'station (landing frequency {p[space] * 100:.1f}% of rolls)'
        elif s.kind is Kind.UTILITY:
            f, why = prm.utility_factor, 'the utility earns little'
        else:
            f, why = rel, f'landing frequency is {p[space] * 100:.1f}% of rolls'
        cash = self._auction_cash(state, me) - self._reserve(state, me)
        return int(min(s.price * f, cash)), why

    def _auction(self, state: GameState, d: Decision) -> Choice:
        a = state.auction
        limit, why = self._limit(state, d.player, a.space)
        bid = self._bid(state, d, limit)
        if bid is not None:
            return Choice(bid, f'Raise the bid to ${a.high_bid + bid.amount}: {why} (my valuation is ${limit})')
        return Choice(Action(ActionKind.PASS_BID), f'Pass. My valuation is ${max(limit, 0)} ({why})')

    def _jail(self, state: GameState, d: Decision) -> Choice | None:
        me = d.player
        unowned = sum(1 for i in BUYABLE if state.owner[i] is None)
        exp, _ = exposure(state, me, 10)
        if unowned >= self.params.jail_unowned and exp < self.params.jail_exp:
            for a, why in (
                (Action(ActionKind.USE_JAIL_CARD), f'Use a jail release card. Unowned properties: {unowned} properties'),
                (Action(ActionKind.PAY_JAIL_FINE), f'Pay the jail fine. Available properties: {unowned} properties, with rent exposure of only ${exp:.0f}'),
            ):
                if a in d.actions:
                    return Choice(a, why)
            return None
        return Choice(ROLL, f'Stay in jail. Leaving risks expected rent of ${exp:.0f} in one roll')

    def _build_gain(self, state: GameState, me: int, a: Action) -> float:
        lvl = state.buildings[a.space]
        return (income(state, me, a.space, lvl + 1) - income(state, me, a.space)) / BOARD[a.space].house_cost

    def _manage(self, state: GameState, d: Decision) -> Choice | None:
        me = d.player
        cash = state.players[me].cash
        res = self._reserve(state, me)
        acts = d.actions
        builds = [a for a in acts if a.kind is ActionKind.BUILD]

        rival_needs = any(
            all(state.owner[i] == o for i in g) and max(state.buildings[i] for i in g) < 3
            for g in GROUPS.values() for o in {state.owner[g[0]]} if o is not None and o != me
        )
        if state.bank_houses <= 6 and rival_needs:
            builds = [a for a in builds if state.buildings[a.space] + 1 < HOTEL]
        if builds:
            best = max(builds, key=lambda a: self._build_gain(state, me, a))
            gain = self._build_gain(state, me, best) * BOARD[best.space].house_cost
            cost = BOARD[best.space].house_cost
            res = max(res, self._build_floor(state, me))
            if cash - cost >= res:
                return Choice(best, f'Build on {BOARD[best.space].name}. Best return, +${gain:.1f} rent per rival roll for ${cost}')

            loose = [a for a in acts if a.kind is ActionKind.MORTGAGE and not _in_full_group(state, me, a.space)]
            if loose:
                m = min(loose, key=lambda a: income(state, me, a.space) / BOARD[a.space].mortgage_value)
                lost = income(state, me, m.space)
                if cash + BOARD[m.space].mortgage_value - cost >= res and gain > self.params.mortgage_gain_ratio * lost:
                    return Choice(m, f'Mortgage {BOARD[m.space].name} to build. The house adds ${gain:.1f} per roll, the property loses ${lost:.1f}')

        for a in acts:
            if a.kind is ActionKind.UNMORTGAGE and not builds:
                if cash - unmortgage_cost(a.space) >= res + self.params.unmortgage_margin:
                    return Choice(a, f'Repay the mortgage on {BOARD[a.space].name}. Large cash buffer')
        return None

    def _raise_funds(self, state: GameState, d: Decision) -> Choice:
        me = d.player
        mortgages = [a for a in d.actions if a.kind is ActionKind.MORTGAGE]
        if mortgages:
            a = min(mortgages, key=lambda a: (_in_full_group(state, me, a.space), income(state, me, a.space) / BOARD[a.space].mortgage_value))
            return Choice(a, f'Mortgage {BOARD[a.space].name}. Smallest expected rent loss (${income(state, me, a.space):.1f} per roll)')
        sells = [a for a in d.actions if a.kind is ActionKind.SELL_BUILDING]
        if sells:
            a = min(sells, key=lambda a: income(state, me, a.space) - income(state, me, a.space, max(0, state.buildings[a.space] - 1)) if a.space is not None else 0)
            return Choice(a, 'Sell the lowest-income building')
        return super()._raise_funds(state, d)

    def _candidates(self, state: GameState, pid: int) -> list:
        def gain(o) -> float:
            groups = {BOARD[s].group for s in o.get_spaces if BOARD[s].group}
            return sum(landing()[i] * BOARD[i].rents[3] for g in groups for i in GROUPS[g])

        return sorted(proposals(state, pid, self.params.min_reserve), key=lambda o: -gain(o))
