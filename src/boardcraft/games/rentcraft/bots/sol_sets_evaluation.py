"""Estimate income, funding gaps and rival insolvency for hypothetical holdings."""
from bisect import bisect_right
from dataclasses import dataclass
from functools import lru_cache
from itertools import count

from ..board import BOARD, GROUPS
from ..state import GameState
from ..board import (
    BUYABLE, GO_SALARY, GO_TO_JAIL_INDEX, HOTEL, JAIL_FINE, JAIL_INDEX, MAX_JAIL_TURNS, RAILROAD_RENTS, RAILROADS,
    UTILITIES, UTILITY_MULTIPLIERS, Kind,
)
from ..buildings import unmortgage_cost
from ..cards import DECKS
from ..debt import interest


COLORS = ("brown", "light_blue", "pink", "orange", "red", "yellow", "green", "dark_blue")
_GROUP_PRICE = {g: sum(BOARD[s].price for s in sp) for g, sp in GROUPS.items()}
_HC = {g: BOARD[sp[0]].house_cost for g, sp in GROUPS.items()}
_PROPS = tuple(s.index for s in BOARD if s.kind is Kind.PROPERTY)
_RR = frozenset(RAILROADS)
_UT = frozenset(UTILITIES)
_M = tuple(s.mortgage_value for s in BOARD)
_U = tuple(unmortgage_cost(i) if BOARD[i].kind in (Kind.PROPERTY, Kind.RAILROAD, Kind.UTILITY) else 0 for i in range(40))
_J = tuple(interest(i) if BOARD[i].kind in (Kind.PROPERTY, Kind.RAILROAD, Kind.UTILITY) else 0 for i in range(40))


@dataclass(frozen=True)
class SolSetsParams:
    trade_variation: float = 0.0
    random_move_probability: float = 0.0

    opening_reserve: int = 250
    developed_reserve: int = 300
    threat_reserve: int = 400

    minimum_cash: int = 150
    cash_share: float = 0.5

    minimum_buffer: int = 100
    buffer_share: float = 0.15

    offensive_cash_floor: int = 200
    knockout_threshold: float = 0.10

    income_horizon: float = 6.0
    nearby_rent_weight: float = 0.30
    baseline_rent_weight: float = 0.70
    asset_weight: float = 0.80
    knockout_weight: float = 800.0
    reserve_gap_weight: float = 2.0
    liquidation_risk_weight: float = 2000.0
    partner_gain_weight: float = 1.10
    offer_gain_threshold: int = 25
    rival_risk_threshold: float = 0.05
    rescue_factor: float = 0.5
    threat_percentile: float = 0.95
    strong_rent: int = 250

    color_weights: tuple = (0.65, 1.25, 1.15, 1.30, 1.00, 1.00, 0.80, 0.90)
    station_bonuses: tuple = (0, 25, 75, 125, 150)
    utility_bonuses: tuple = (0, 20, 40)
    blocking_bonus_cap: int = 800
    blocking_share: float = 0.5
    weak_blocking_share: float = 0.15
    weak_blocking_cap: int = 250
    urgent_card_value: int = 50
    acquisition_card_value: int = 40
    developed_card_value: int = 25
    acquisition_unowned: int = 6

    cash_grid: int = 25
    offer_skeletons: int = 24
    planning_states: int = 256
    ordinary_search_factor: float = 1.5
    key_search_factor: float = 5.0
    concession: float = 0.5
    retry_cash_change: int = 100
    rescue_package: int = 2

    rival_property_estimate: float = 1.5
    last_property_estimate: float = 2.5

    auction_block_factor: float = 0.75

    cheap_property: int = 200

    bail_cash_floor: int = 200

    endgame_rounds: int = 2

    management_steps: int = 40


DEFAULT_PARAMS = SolSetsParams()


def color_weight(p: SolSetsParams, group: str) -> float:
    w = p.color_weights
    i = COLORS.index(group)
    return float(w[i]) if i < len(w) else 1.0


_BOARD_IDS = count()


@lru_cache(maxsize=400_000)
def _board_id(key: tuple) -> int:
    """Intern a board layout without reusing identifiers after cache eviction."""
    return next(_BOARD_IDS)


class Snap:
    """A candidate set of assets, cash, debts, physical buildings and transfer fees."""
    __slots__ = ("owner", "bld", "mort", "cash", "debts", "houses", "hotels", "cards", "fresh", "_key", "_bid")

    def __init__(self, owner, bld, mort, cash, debts, houses, hotels, cards, fresh=frozenset()):
        self.owner = owner
        self.bld = bld
        self.mort = mort
        self.cash = cash
        self.debts = debts
        self.houses = houses
        self.hotels = hotels
        self.cards = cards
        self.fresh = fresh
        self._key = None
        self._bid = None

    @classmethod
    def from_state(cls, state: GameState) -> "Snap":
        debts = [(d.debtor, d.creditor, d.amount) for d in state.debts]
        w = state.window
        if w is not None:
            debts += [(p[0], p[1], p[2]) for p in w.payments]
        debts += [(p[0], p[1], p[2]) for p in state.pending_payments]
        debts += [(pid, None, amt) for pid, _sp, amt in state.takeover_owed]

        debts += [(pid, None, _J[sp]) for pid, sp in state.pending_takeovers]
        fresh = frozenset(sp for _pid, sp in state.pending_takeovers)
        return cls(list(state.owner), list(state.buildings), list(state.mortgaged), [p.cash for p in state.players],
                   debts, state.bank_houses, state.bank_hotels, [len(p.jail_cards) for p in state.players], fresh)

    def copy(self) -> "Snap":
        return Snap(list(self.owner), list(self.bld), list(self.mort), list(self.cash), list(self.debts), self.houses,
                    self.hotels, list(self.cards), self.fresh)

    def cash_copy(self) -> "Snap":
        s = Snap(self.owner, self.bld, self.mort, list(self.cash), list(self.debts), self.houses, self.hotels,
                 self.cards, self.fresh)
        s._key = self._key
        s._bid = self._bid
        return s

    def bid(self) -> int:
        b = self._bid
        if b is None:
            b = _board_id(self.board_key())
            self._bid = b
        return b

    def board_key(self) -> tuple:
        k = self._key
        if k is None:
            k = self._key = (tuple(self.owner), tuple(self.bld), tuple(self.mort), tuple(self.cards))
        return k

    def full_groups(self, pid: int) -> list[str]:
        return _full_groups_cached(self.board_key()[0], pid)

    def debt_of(self, pid: int) -> int:
        return sum(a for d, _c, a in self.debts if d == pid)

    def debt_vector(self, n: int) -> tuple:
        out = [0] * n
        for d, _c, a in self.debts:
            out[d] += a
        return tuple(out)

    def settle(self, alive) -> None:
        if not self.debts:
            return
        debtors = {d for d, _c, _a in self.debts}
        for d in sorted(debtors):
            owed = self.debt_of(d)
            if self.cash[d] >= owed:
                self.cash[d] -= owed
                for dd, c, a in self.debts:
                    if dd == d and c is not None and alive[c]:
                        self.cash[c] += a
                self.debts = [x for x in self.debts if x[0] != d]

    def unmortgage_price(self, sp: int) -> int:
        return _U[sp] - (_J[sp] if sp in self.fresh else 0)


def full_group(owner, pid: int, group: str) -> bool:
    return all(owner[i] == pid for i in GROUPS[group])


def full_groups(owner, pid: int) -> list[str]:
    return [g for g, sp in GROUPS.items() if all(owner[i] == pid for i in sp)]


@lru_cache(maxsize=65536)
def _full_groups_cached(owner: tuple, pid: int) -> list[str]:
    return full_groups(owner, pid)


def group_built(bld, group: str) -> bool:
    return any(bld[i] for i in GROUPS[group])


def _dice_pairs():
    return [(a, b) for a in range(1, 7) for b in range(1, 7)]


@lru_cache(maxsize=None)
def _sum_probs() -> tuple:
    out: dict[int, float] = {}
    for a, b in _dice_pairs():
        out[a + b] = out.get(a + b, 0.0) + 1 / 36
    return tuple(sorted(out.items()))


def _nearest(pos: int, targets) -> int:
    return min(targets, key=lambda t: (t - pos) % 40)


def _land(t: int, go: int, dice: int, held: frozenset, others: int, depth: int = 0) -> list:
    if t == GO_TO_JAIL_INDEX:
        return [(1.0, go, 0, 0, 0, -1, 0, 0)]
    s = BOARD[t]
    if t in BUYABLE:
        return [(1.0, go, 0, 0, 0, t, 0, dice if s.kind is Kind.UTILITY else 0)]
    if s.kind is Kind.TAX:
        return [(1.0, go, s.tax, 0, 0, -1, 0, 0)]
    if s.kind in (Kind.OPPORTUNITY, Kind.COMMUNITY_FUND) and depth < 3:
        name = 'opportunity' if s.kind is Kind.OPPORTUNITY else 'community_fund'
        deck = [c for c in (DECKS["opportunity"] if name == 'opportunity' else DECKS["community_fund"]) if not (c.effect == "jail_free" and name in held)]
        w = 1.0 / len(deck)
        out = []
        for c in deck:
            e = c.effect
            if e == "advance_to":
                g2 = go + (GO_SALARY if c.target < t or c.target == 0 else 0)
                sub = _land(c.target, g2, dice, held, others, depth + 1) if c.target != 0 else [(1.0, g2, 0, 0, 0, -1, 0, 0)]
            elif e == "advance_nearest_railroad":
                r = _nearest(t, RAILROADS)
                sub = [(1.0, go + (GO_SALARY if r < t else 0), 0, 0, 0, r, 1, 0)]
            elif e == "advance_nearest_utility":
                u = _nearest(t, UTILITIES)
                g2 = go + (GO_SALARY if u < t else 0)
                sub = [(pd, g2, 0, 0, 0, u, 2, sm) for sm, pd in _sum_probs()]
            elif e == "back_three":
                sub = _land((t - 3) % 40, go, dice, held, others, depth + 1)
            elif e == "pay":
                sub = [(1.0, go, c.value, 0, 0, -1, 0, 0)]
            elif e == "pay_each":
                sub = [(1.0, go, c.value * others, 0, 0, -1, 0, 0)]
            elif e == "repairs":
                sub = [(1.0, go, 0, c.value, c.value2, -1, 0, 0)]
            else:
                sub = [(1.0, go, 0, 0, 0, -1, 0, 0)]
            out.extend((w * b[0],) + b[1:] for b in sub)
        return out
    return [(1.0, go, 0, 0, 0, -1, 0, 0)]


@lru_cache(maxsize=4096)
def branches(pos: int, in_jail: bool, jail_turns: int, doubles2: bool, held: frozenset, others: int) -> tuple:
    raw: dict[tuple, float] = {}
    for a, b in _dice_pairs():
        p = 1 / 36
        s = a + b
        dbl = a == b
        if in_jail:
            if dbl:
                start, fixed = JAIL_INDEX, 0
            elif jail_turns >= MAX_JAIL_TURNS - 1:
                start, fixed = JAIL_INDEX, JAIL_FINE
            else:
                key = (0, 0, 0, 0, -1, 0, 0)
                raw[key] = raw.get(key, 0.0) + p
                continue
        else:
            if dbl and doubles2:
                key = (0, 0, 0, 0, -1, 0, 0)
                raw[key] = raw.get(key, 0.0) + p
                continue
            start, fixed = pos, 0
        t = (start + s) % 40
        go = GO_SALARY if start + s >= 40 else 0
        for br in _land(t, go, s, held, others):
            key = (br[1], br[2] + fixed, br[3], br[4], br[5], br[6], br[7])
            raw[key] = raw.get(key, 0.0) + p * br[0]
    return tuple((pr,) + k for k, pr in sorted(raw.items()))


class Ctx:
    """Roll probabilities and game conditions shared by all plans for one decision."""
    def __init__(self, state: GameState, params: SolSetsParams = DEFAULT_PARAMS):
        self.params = params
        players = state.players
        self.n = len(players)
        self.alive = tuple(not p.bankrupt for p in players)
        self.n_alive = sum(self.alive)
        self.pos = tuple(p.position for p in players)
        self.jail = tuple((p.in_jail, p.jail_turns) for p in players)
        held = frozenset(d for p in players for d in p.jail_cards)
        self.held = held
        self.current = state.current
        self.doubles2 = bool(state.extra_roll and state.doubles >= 2)
        mr = state.config.max_rounds
        self.rounds_left = None if mr is None else max(0, mr - state.round + 1)
        self.endgame = self.rounds_left is not None and self.rounds_left <= params.endgame_rounds
        self.key = (self.alive, self.pos, self.jail, held, self.current, self.doubles2, self.rounds_left, params)
        others = self.n_alive - 1
        self.branches = tuple(
            branches(self.pos[i], self.jail[i][0], self.jail[i][1], self.doubles2 and i == self.current, held, others)
            if self.alive[i] else () for i in range(self.n))
        self._derive()

    def _derive(self) -> None:
        self.spaces = tuple(tuple(sorted({b[5] for b in br if b[5] >= 0})) for br in self.branches)
        pl = []
        for br in self.branches:
            d: dict = {}
            for b in br:
                if b[5] >= 0:
                    d[b[5]] = d.get(b[5], 0.0) + b[0]
            pl.append(d)
        self.p_land = tuple(pl)

    def next_roller(self, me: int) -> int | None:
        if self.current != me and self.alive[self.current]:
            return self.current
        for k in range(1, self.n):
            j = (self.current + k) % self.n
            if self.alive[j] and j != me:
                return j
        return None


def long_rent(owner, bld, mort, t: int) -> float:
    o = owner[t]
    if o is None or mort[t]:
        return 0.0
    s = BOARD[t]
    if s.kind is Kind.PROPERTY:
        lv = bld[t]
        if lv:
            return float(s.rents[lv])
        return float(s.rents[0] * (2 if full_group(owner, o, s.group) else 1))
    if s.kind is Kind.RAILROAD:
        return float(RAILROAD_RENTS[sum(1 for i in RAILROADS if owner[i] == o) - 1])
    return float(UTILITY_MULTIPLIERS[sum(1 for i in UTILITIES if owner[i] == o) - 1] * 7)


def mortgage_class(owner, bld, mort, pid: int, sp: int) -> int:
    s = BOARD[sp]
    if s.group is None:
        return 2
    g = GROUPS[s.group]
    if all(owner[i] == pid for i in g):
        return 5
    others = [owner[i] for i in g if i != sp]
    if others and others[0] is not None and others[0] != pid and all(o == others[0] for o in others):
        return 3
    mine = sum(1 for i in g if owner[i] == pid)
    missing = [i for i in g if owner[i] != pid]
    if mine * 2 >= len(g) and (all(owner[i] is None for i in missing) or len(missing) == 1):
        return 4
    return 1


class BoardEval:
    """Income, mortgage capacity and distributions of near-term expenses by player."""
    __slots__ = ("n", "alive", "bank_extra", "safe_mort", "assets", "nw", "option", "repair_max", "threat",
                 "need_vals", "need_tail", "near_in", "near_cost", "long_base", "long_cost", "kill", "blocks",
                 "engine", "launch", "launch3", "card_first", "p_land", "houses_of", "hotels_of", "_memo")

    def __init__(self, snap: Snap, ctx: Ctx):
        P = ctx.params
        n = ctx.n
        alive = ctx.alive
        owner, bld, mort = snap.owner, snap.bld, snap.mort
        self.n, self.alive = n, alive
        bank_extra = [0.0] * n
        safe_mort = [0.0] * n
        assets = [0.0] * n
        nw = [0.0] * n
        houses = [0] * n
        hotels = [0] * n
        long_base = [0.0] * n
        full_cache: dict[tuple, bool] = {}

        def is_full(pid, g):
            k = (pid, g)
            v = full_cache.get(k)
            if v is None:
                v = full_cache[k] = all(owner[i] == pid for i in GROUPS[g])
            return v

        built_g = {g: any(bld[i] for i in sp) for g, sp in GROUPS.items()}
        long_r = [0.0] * 40
        rr_count = [0] * n
        ut_count = [0] * n
        for t in BUYABLE:
            o = owner[t]
            if o is None:
                continue
            if t in _RR:
                rr_count[o] += 1
            elif t in _UT:
                ut_count[o] += 1
        for t in BUYABLE:
            o = owner[t]
            if o is None:
                continue
            s = BOARD[t]
            lv = bld[t]
            hc = s.house_cost
            bank_extra[o] += lv * (hc // 2) + (0 if mort[t] else _M[t])
            assets[o] += (_M[t] if mort[t] else s.price) + lv * hc
            nw[o] += (s.price // 2 if mort[t] else s.price) + lv * hc
            if lv == HOTEL:
                hotels[o] += 1
            else:
                houses[o] += lv
            if not mort[t] and not (s.group is not None and (built_g[s.group] or is_full(o, s.group))):
                safe_mort[o] += _M[t]
            if not mort[t]:
                if s.kind is Kind.PROPERTY:
                    r = float(s.rents[lv]) if lv else float(s.rents[0] * (2 if is_full(o, s.group) else 1))
                elif s.kind is Kind.RAILROAD:
                    r = float(RAILROAD_RENTS[rr_count[o] - 1])
                else:
                    r = float(UTILITY_MULTIPLIERS[ut_count[o] - 1] * 7)
                long_r[t] = r
                if alive[o]:
                    long_base[o] += r / 40.0
        long_all = sum(long_base)
        self.long_base = long_base
        self.long_cost = [long_all - long_base[i] for i in range(n)]
        self.bank_extra, self.safe_mort, self.assets, self.nw = bank_extra, safe_mort, assets, nw
        self.houses_of, self.hotels_of = houses, hotels
        self.repair_max = [max(25 * houses[i] + 100 * hotels[i], 40 * houses[i] + 115 * hotels[i]) for i in range(n)]


        option = [0.0] * n
        for g, sp in GROUPS.items():
            cnt = {}
            for i in sp:
                o = owner[i]
                if o is not None:
                    cnt[o] = cnt.get(o, 0) + 1
            for o, c in cnt.items():
                if c == len(sp):
                    if built_g[g]:
                        prog = 0.05 if all(bld[i] >= 3 for i in sp) else 0.20
                    else:
                        prog = 0.30
                    avail = 1.0
                else:
                    prog = 0.08 if c * 2 < len(sp) else 0.20
                    missing = [owner[i] for i in sp if owner[i] != o]
                    avail = 0.70 if all(m is None for m in missing) else 0.40
                option[o] += color_weight(P, g) * _GROUP_PRICE[g] * prog * avail
        for o in range(n):
            ro, uo = P.station_bonuses, P.utility_bonuses
            option[o] += float(ro[min(rr_count[o], len(ro) - 1)]) + float(uo[min(ut_count[o], len(uo) - 1)])
        self.option = option


        engine = [False] * n
        launch: list[list] = [[] for _ in range(n)]
        launch3: list[list] = [[] for _ in range(n)]
        strong = P.strong_rent
        for g, sp in GROUPS.items():
            o = owner[sp[0]]
            if o is None or not is_full(o, g):
                continue
            hc = _HC[g]
            excl = sum((0 if mort[i] else _M[i]) + bld[i] * (hc // 2) for i in sp)
            unm = sum(_U[i] for i in sp if mort[i])
            if not unm and all(bld[i] and BOARD[i].rents[bld[i]] >= strong for i in sp):
                engine[o] = True
            k = next((k for k in range(1, 6) if all(BOARD[i].rents[k] >= strong for i in sp)), None)
            if k is not None:
                cost = sum(max(0, k - bld[i]) for i in sp) * hc + unm
                launch[o].append((cost, excl, g))
            if all(BOARD[i].rents[3] >= strong for i in sp):
                cost3 = sum(max(0, 3 - bld[i]) for i in sp) * hc + unm
                launch3[o].append((cost3, excl, g))
        self.engine, self.launch, self.launch3 = engine, launch, launch3


        blocks: list[list] = [[] for _ in range(n)]
        for g, sp in GROUPS.items():
            owners = [owner[i] for i in sp]
            for j in set(o for o in owners if o is not None):
                if not alive[j]:
                    continue
                rest = [i for i in sp if owner[i] != j]
                if len(rest) != 1:
                    continue
                b = owner[rest[0]]
                if b is None or b == j:
                    continue
                cost3 = 3 * len(sp) * _HC[g]
                need = cost3 + sum(_U[i] for i in sp if mort[i])
                excl = sum(_M[i] for i in sp if owner[i] == j and not mort[i])
                full_v = min(P.blocking_bonus_cap, P.blocking_share * cost3)
                weak_v = min(P.weak_blocking_cap, P.weak_blocking_share * cost3)
                blocks[b].append((need, excl, j, full_v, weak_v))
        self.blocks = blocks


        free = sum(1 for t in BUYABLE if owner[t] is None)
        card_value = [0.0] * n
        for i in range(n):
            acq = not engine[i] and free >= P.acquisition_unowned
            if acq and ctx.jail[i][0]:
                first = P.urgent_card_value
            elif acq:
                first = P.acquisition_card_value
            else:
                first = P.developed_card_value
            k = snap.cards[i]

            card_value[i] = (first if k >= 1 else 0) + ((first // 2) // 5 * 5 if k >= 2 else 0)
        self.card_first = card_value


        near_in = [[0.0] * n for _ in range(n)]
        near_cost = [0.0] * n
        need_vals: list = [None] * n
        need_tail: list = [None] * n
        threat = [0.0] * n
        kill: dict = {}
        p_land = [None] * n
        for i in range(n):
            if not alive[i]:
                need_vals[i], need_tail[i] = [], [0.0]
                continue
            sig = []
            for sp in ctx.spaces[i]:
                o = owner[sp]
                if o is None or o == i or mort[sp] or not alive[o]:
                    sig.append(None)
                elif sp in _UT:
                    sig.append((o, -UTILITY_MULTIPLIERS[ut_count[o] - 1]))
                elif sp in _RR:
                    sig.append((o, RAILROAD_RENTS[rr_count[o] - 1]))
                else:
                    sig.append((o, long_r[sp]))
            key = (ctx.key, i, tuple(sig), houses[i], hotels[i], P.threat_percentile)
            res = _PAYER_CACHE.get(key)
            if res is None:
                if len(_PAYER_CACHE) > 50000:
                    _PAYER_CACHE.clear()
                res = _PAYER_CACHE[key] = _payer(ctx, i, dict(zip(ctx.spaces[i], sig)), houses[i], hotels[i],
                                                 P.threat_percentile)
            by_owner, cost_i, vals, tail, thr, kills = res
            for o, v in by_owner:
                near_in[o][i] = v
            near_cost[i] = cost_i
            need_vals[i], need_tail[i], threat[i] = vals, tail, thr
            for o, kl in kills:
                kill[(o, i)] = kl
            p_land[i] = ctx.p_land[i]
        self.near_in, self.near_cost = near_in, near_cost
        self.need_vals, self.need_tail, self.threat = need_vals, need_tail, threat
        self.p_land = p_land
        self.kill = kill
        self._memo = {}

    @staticmethod
    def _p_above(vals, tail, x: float) -> float:
        return tail[bisect_right(vals, x)]

    def bank(self, i: int, cash) -> float:
        return cash[i] + self.bank_extra[i]

    def q(self, i: int, cash, debt) -> float:
        return self._p_above(self.need_vals[i], self.need_tail[i], cash[i] + self.safe_mort[i] - debt[i])

    def kill_raw(self, i: int, cash, debt) -> float:
        tot = 0.0
        for j in range(self.n):
            if j == i or not self.alive[j]:
                continue
            kl = self.kill.get((i, j))
            if kl:
                tot += self._p_above(kl[0], kl[1], cash[j] + self.bank_extra[j] - debt[j])
        return tot

    def threatening(self, j: int, cash, debt) -> bool:
        if self.engine[j]:
            return True
        launch = self.launch[j]
        if not launch:
            return False
        b = cash[j] + self.bank_extra[j] - debt[j]
        return any(cost <= b - excl for cost, excl, _g in launch)

    def can_three(self, j: int, group: str, cash, debt) -> bool:
        b = cash[j] + self.bank_extra[j] - debt[j]
        return any(g == group and cost <= b - excl for cost, excl, g in self.launch3[j])

    def any_threat(self, me: int, cash, debt) -> bool:
        for j in range(self.n):
            if j != me and self.alive[j] and self.threatening(j, cash, debt):
                return True
        return False

    def reserve(self, i: int, cash, debt, P: SolSetsParams) -> float:
        if self.any_threat(i, cash, debt):
            base = P.threat_reserve
        elif self.engine[i]:
            base = P.developed_reserve
        else:
            base = P.opening_reserve
        return float(max(base, self.repair_max[i], self.threat[i]))

    def _rental_projection(self, i: int, cash, debt, P: SolSetsParams) -> tuple[float, float]:
        if P.income_horizon == 0:
            return 0.0, 0.0
        inc = 0.0
        for j in range(self.n):
            if j == i or not self.alive[j]:
                continue
            cap = max(0.0, cash[j] + self.bank_extra[j] - debt[j]) / P.income_horizon
            v = P.nearby_rent_weight * self.near_in[i][j] + P.baseline_rent_weight * self.long_base[i]
            inc += v if v < cap else cap
        cost = P.nearby_rent_weight * self.near_cost[i] + P.baseline_rent_weight * self.long_cost[i]
        return inc, cost

    def strategic(self, i: int, cash, debt, ctx: Ctx) -> float:
        P = ctx.params
        inc, cost = self._rental_projection(i, cash, debt, P)
        kill = self.kill_raw(i, cash, debt) * (1.0 if ctx.n_alive <= 2 else P.rescue_factor)
        blk = 0.0
        for need, excl, j, full_v, weak_v in self.blocks[i]:
            blk += full_v if cash[j] + self.bank_extra[j] - debt[j] - excl >= need else weak_v
        return P.income_horizon * (inc - cost) + self.option[i] + blk + self.card_first[i] + P.knockout_weight * kill

    def value(self, i: int, cash, debt, ctx: Ctx, detail: bool = False):
        P = ctx.params
        n, alive = self.n, self.alive
        inc, cost = self._rental_projection(i, cash, debt, P)
        q = self.q(i, cash, debt)
        kill = self.kill_raw(i, cash, debt) * (1.0 if ctx.n_alive <= 2 else P.rescue_factor)
        if ctx.endgame:
            nw_i = cash[i] - debt[i] + self.nw[i]
            rounds = min(ctx.rounds_left or 0, P.income_horizon)
            leading = all(nw_i >= cash[j] - debt[j] + self.nw[j] for j in range(n) if j != i and alive[j])
            v = nw_i + rounds * (inc - cost) - P.liquidation_risk_weight * q + (0.0 if leading else P.knockout_weight * kill)
            return (v, {"q": q, "kill": kill}) if detail else v
        r = self.reserve(i, cash, debt, P)
        gap = max(0.0, r - (cash[i] + self.safe_mort[i]))
        blk = 0.0
        for need, excl, j, full_v, weak_v in self.blocks[i]:
            blk += full_v if cash[j] + self.bank_extra[j] - debt[j] - excl >= need else weak_v
        v = (cash[i] - debt[i] + P.asset_weight * self.assets[i] + P.income_horizon * (inc - cost) + self.option[i]
             + blk + self.card_first[i] + P.knockout_weight * kill - P.reserve_gap_weight * gap - P.liquidation_risk_weight * q)
        if detail:
            return v, {"q": q, "kill": kill, "R": r, "gap": gap, "income": inc, "cost": cost, "block": blk}
        return v


_PAYER_CACHE: dict = {}


def _tails(d: dict) -> tuple[list, list]:
    vals = sorted(d)
    tail = [0.0] * (len(vals) + 1)
    acc = 0.0
    for k in range(len(vals) - 1, -1, -1):
        acc += d[vals[k]]
        tail[k] = acc
    return vals, tail


def _payer(ctx, i: int, sig: dict, h_i: int, H_i: int, pct: float) -> tuple:
    needs: dict = {}
    near: dict = {}
    kill_lists: dict = {}
    cost = 0.0
    for (pr, go, fixed, rh, rH, sp, mode, dice) in ctx.branches[i]:
        rent = 0.0
        o = None
        if sp >= 0:
            x = sig[sp]
            if x is not None:
                o, val = x
                if mode == 2:
                    rent = 10.0 * dice
                elif sp in _UT:
                    rent = float(-val * dice)
                elif sp in _RR:
                    rent = float(val * (2 if mode == 1 else 1))
                else:
                    rent = val
        need = fixed + rent + (rh * h_i + rH * H_i if (rh or rH) else 0) - go
        if need < 0:
            need = 0.0
        needs[need] = needs.get(need, 0.0) + pr
        if rent:
            near[o] = near.get(o, 0.0) + pr * rent
            cost += pr * rent
            kl = kill_lists.setdefault(o, {})
            kl[need] = kl.get(need, 0.0) + pr
    vals, tail = _tails(needs)
    cum = 0.0
    thr = 0.0
    for v in vals:
        cum += needs[v]
        thr = v
        if cum >= pct - 1e-9:
            break
    return (tuple(near.items()), cost, vals, tail, thr, tuple((o, _tails(d)) for o, d in kill_lists.items()))


_EVAL_PER_CTX = 6000


@lru_cache(maxsize=6)
def _context_cache(key):
    return {}


def board_eval(snap: Snap, ctx: Ctx) -> BoardEval:
    per = _context_cache(ctx.key)
    key = snap.bid()
    be = per.get(key)
    if be is None:
        if len(per) >= _EVAL_PER_CTX:
            per.clear()
        be = per[key] = BoardEval(snap, ctx)
    return be
