"""Search feasible development, mortgage and trade plans for Sol Sets."""
from dataclasses import dataclass
from functools import lru_cache

from ..board import BOARD, GROUPS, HOTEL
from ..trade import TradeOffer
from .sol_sets_evaluation import (
    COLORS, _J, _M, Ctx, Snap, SolSetsParams, board_eval, group_built, long_rent, mortgage_class,
)

_ORDER = {g: k for k, g in enumerate(COLORS)}


@dataclass
class Plan:
    snap: Snap
    v: float
    q: float
    steps: tuple = ()
    offensive: bool = False
    colors: tuple = ()

    def key(self):
        return (-self.v, self.q, -sum(self.snap.cash), self.steps)


def color_targets(snap: Snap, group: str) -> list[tuple]:
    return _targets(group, tuple(snap.bld[i] for i in GROUPS[group]))


@lru_cache(maxsize=4096)
def _targets(group: str, cur: tuple) -> list[tuple]:
    sp = GROUPS[group]
    n = len(sp)
    out = []
    for total in range(sum(cur) + 1, 5 * n + 1):
        base, extra = divmod(total, n)
        order = sorted(range(n), key=lambda k: (-cur[k], -BOARD[sp[k]].rents[min(HOTEL, base + 1)], sp[k]))
        lv = [base] * n
        for k in order[:extra]:
            lv[k] += 1
        if all(lv[k] >= cur[k] for k in range(n)):
            out.append(tuple(lv))
    return out


def bank_after(houses: int, hotels: int, cur, lv) -> tuple[int, int] | None:
    new_hotels = sum(1 for c, t in zip(cur, lv) if t == HOTEL and c < HOTEL)
    if new_hotels:
        peak = sum(4 - c for c in cur if c < 4)
        if peak > houses or new_hotels > hotels:
            return None
        return houses - peak + 4 * new_hotels, hotels - new_hotels
    need = sum(t - c for c, t in zip(cur, lv))
    if need > houses:
        return None
    return houses - need, hotels


_CACHE: dict = {}


def _cached(key, make):
    v = _CACHE.get(key, _CACHE)
    if v is _CACHE:
        if len(_CACHE) > 300000:
            _CACHE.clear()
        v = _CACHE[key] = make()
    return v


def mortgage_order(snap: Snap, pid: int, exclude_groups=()) -> list[int]:
    return _cached(("mo", snap.bid(), pid, tuple(exclude_groups)), lambda: _mortgage_order(snap, pid, exclude_groups))


def _mortgage_order(snap: Snap, pid: int, exclude_groups) -> list[int]:
    owner, bld, mort = snap.owner, snap.bld, snap.mort
    out = []
    for sp in range(40):
        if owner[sp] != pid or mort[sp]:
            continue
        g = BOARD[sp].group
        if g is not None and (g in exclude_groups or group_built(bld, g)):
            continue
        cls = mortgage_class(owner, bld, mort, pid, sp)
        if cls >= 5:
            continue
        out.append((cls, long_rent(owner, bld, mort, sp) / max(1, _M[sp]), sp))
    out.sort()
    return [sp for _c, _r, sp in out]


def _build_board(snap: Snap, group: str, lv: tuple):
    sp = GROUPS[group]
    cur = [snap.bld[i] for i in sp]
    bank = bank_after(snap.houses, snap.hotels, cur, lv)
    if bank is None:
        return None
    cost = sum(t - c for c, t in zip(cur, lv)) * BOARD[sp[0]].house_cost
    cost += sum(snap.unmortgage_price(i) for i in sp if snap.mort[i])
    bld = list(snap.bld)
    mort = list(snap.mort)
    for i, t in zip(sp, lv):
        bld[i] = t
        mort[i] = False
    fresh = frozenset(x for x in snap.fresh if x not in sp)
    key = (snap.board_key()[0], tuple(bld), tuple(mort), snap.board_key()[3])
    tmpl = Snap(snap.owner, bld, mort, [], [], bank[0], bank[1], snap.cards, fresh)
    tmpl._key = key
    tmpl.bid()
    return bld, mort, bank, cost, fresh, key, tmpl._bid


def apply_build(snap: Snap, group: str, lv: tuple) -> Snap | None:
    sp = GROUPS[group]
    res = _cached(("ab", snap.bid(), snap.houses, snap.hotels, snap.fresh, group, lv),
                  lambda: _build_board(snap, group, lv))
    if res is None:
        return None
    bld, mort, bank, cost, fresh, key, bid = res
    pid = snap.owner[sp[0]]
    cash = list(snap.cash)
    cash[pid] -= cost
    s = Snap(snap.owner, bld, mort, cash, list(snap.debts), bank[0], bank[1], snap.cards, fresh)
    s._key = key
    s._bid = bid
    return s


def apply_mortgages(snap: Snap, pid: int, spaces) -> Snap:
    if not spaces:
        return snap
    spaces = tuple(spaces)

    def make():
        mort = list(snap.mort)
        for sp in spaces:
            mort[sp] = True
        k = snap.board_key()
        t = Snap(snap.owner, snap.bld, mort, [], [], 0, 0, snap.cards)
        t._key = (k[0], k[1], tuple(mort), k[3])
        return mort, t._key, t.bid()

    mort, key, bid = _cached(("am", snap.bid(), spaces), make)
    cash = list(snap.cash)
    cash[pid] += sum(_M[sp] for sp in spaces)
    s = Snap(snap.owner, snap.bld, mort, cash, list(snap.debts), snap.houses, snap.hotels, snap.cards, snap.fresh)
    s._key = key
    s._bid = bid
    return s


def rival_house_needs(snap: Snap, ctx: Ctx, me: int) -> list[int]:
    be = board_eval(snap, ctx)
    debt = snap.debt_vector(ctx.n)
    out = []
    for j in range(ctx.n):
        if j == me or not ctx.alive[j]:
            continue
        b = snap.cash[j] + be.bank_extra[j] - debt[j]
        for g in snap.full_groups(j):
            sp = GROUPS[g]
            if any(snap.bld[i] == HOTEL for i in sp):
                continue
            need3 = sum(max(0, 3 - snap.bld[i]) for i in sp)
            if not need3:
                continue
            hc = BOARD[sp[0]].house_cost
            excl = sum((0 if snap.mort[i] else _M[i]) + snap.bld[i] * (hc // 2) for i in sp)
            cost2 = sum(max(0, 2 - snap.bld[i]) for i in sp) * hc + sum(snap.unmortgage_price(i) for i in sp if snap.mort[i])
            if cost2 <= b - excl:
                out.append(need3)
    return out


def _all_three(snap: Snap, pid: int) -> bool:
    return any(all(snap.bld[i] >= 3 for i in GROUPS[g]) for g in snap.full_groups(pid))


def evaluate(snap: Snap, ctx: Ctx, pid: int) -> tuple[float, float]:
    be = board_eval(snap, ctx)
    debt = snap.debt_vector(ctx.n)
    return be.value(pid, snap.cash, debt, ctx), be.q(pid, snap.cash, debt)


def best_own(snap: Snap, ctx: Ctx, me: int, allow_build: bool = True) -> Plan:
    """Choose an affordable development sequence across the player's complete groups."""
    P = ctx.params
    v0, q0 = evaluate(snap, ctx, me)
    start = Plan(snap, v0, q0)
    groups = [g for g in snap.full_groups(me)] if allow_build else []
    if not groups:
        return start
    groups.sort(key=lambda g: _ORDER[g])
    be0 = board_eval(snap, ctx)
    debt0 = snap.debt_vector(ctx.n)
    kill0 = be0.kill_raw(me, snap.cash, debt0)
    three0 = _all_three(snap, me)
    needs = rival_house_needs(snap, ctx, me)
    houses0 = snap.houses
    blocked0 = [h for h in needs if houses0 < h]
    states = [start]
    for g in groups:
        new = list(states)
        for st in states:
            for lv in color_targets(st.snap, g):
                p = _extend_own(st, g, lv, ctx, me, kill0, three0, blocked0, P)
                if p is not None:
                    new.append(p)
        new.sort(key=Plan.key)
        states = new[: max(1, P.planning_states)]
    best = min(states, key=Plan.key)
    return best


def _extend_own(st: Plan, g: str, lv: tuple, ctx: Ctx, me: int, kill0: float, three0: bool, blocked0: list,
                P: SolSetsParams) -> Plan | None:
    built = apply_build(st.snap, g, lv)
    if built is None:
        return None

    releases = any(built.houses >= h for h in blocked0) and built.houses > st.snap.houses
    be = board_eval(built, ctx)
    debt = built.debt_vector(ctx.n)
    r = be.reserve(me, built.cash, debt, P)
    floor = max(P.minimum_cash, P.cash_share * r)
    order = mortgage_order(built, me)
    c = built.cash[me] - debt[me]
    safe_total = c + be.safe_mort[me]
    morts = None
    offensive = False
    if safe_total >= r:
        morts = _cover(order, c, floor)
    if morts is None:
        morts = _cover(order, c, P.offensive_cash_floor)
        if morts is None:
            return None
        if be.any_threat(me, built.cash, debt):
            return None
        recover = 0
        for gg in set(st.colors) | {g}:
            hc = BOARD[GROUPS[gg][0]].house_cost
            recover += sum(max(0, built.bld[i] - 2) for i in GROUPS[gg]) * (hc // 2)
        if safe_total + recover < r:
            return None
        first_three = not three0 and _all_three(built, me)
        if not first_three:
            kill1 = be.kill_raw(me, built.cash, debt)
            if kill1 - kill0 < P.knockout_threshold:
                return None
        offensive = True
    final = apply_mortgages(built, me, morts)
    fbe = board_eval(final, ctx)
    fdebt = final.debt_vector(ctx.n)
    if releases:
        kill1 = fbe.kill_raw(me, final.cash, fdebt)
        if kill1 - kill0 < P.knockout_threshold:
            return None
    v = fbe.value(me, final.cash, fdebt, ctx)
    q = fbe.q(me, final.cash, fdebt)
    if releases:
        v -= 1e-6
    steps = st.steps + tuple(("M", sp) for sp in morts) + (("B", g, lv),)
    return Plan(final, v, q, steps, st.offensive or offensive, st.colors + (g,))


def _cover(order: list[int], cash: float, floor: float) -> list[int] | None:
    out = []
    c = cash
    for sp in order:
        if c >= floor:
            break
        out.append(sp)
        c += _M[sp]
    return out if c >= floor else None


def aggressive(snap: Snap, ctx: Ctx, j: int, me: int) -> Snap:
    P = ctx.params
    groups = sorted(snap.full_groups(j), key=lambda g: _ORDER[g])
    if not groups or not ctx.alive[j]:
        return snap
    v0, _ = evaluate(snap, ctx, me)
    states = [(v0, (), snap)]
    for g in groups:
        new = list(states)
        for v, steps, s in states:
            for lv in color_targets(s, g):
                built = apply_build(s, g, lv)
                if built is None:
                    continue
                debt = built.debt_vector(ctx.n)
                order = _cached(("ag", built.bid(), j), lambda: _aggressive_order(built, j))
                morts = _cover(order, built.cash[j] - debt[j], 0)
                if morts is None:
                    continue
                final = apply_mortgages(built, j, morts)
                vm, _ = evaluate(final, ctx, me)
                new.append((vm, steps + ((g, lv),), final))
        new.sort(key=lambda t: (t[0], t[1]))
        states = new[: max(1, P.planning_states)]
    return min(states, key=lambda t: (t[0], t[1]))[2]


def _aggressive_order(snap: Snap, pid: int) -> list[int]:
    order = list(mortgage_order(snap, pid))
    return order + [sp for sp in _core_unbuilt(snap, pid) if sp not in order]


def _core_unbuilt(snap: Snap, pid: int) -> list[int]:
    out = []
    for g in snap.full_groups(pid):
        if not group_built(snap.bld, g):
            out += [i for i in GROUPS[g] if not snap.mort[i]]
    return sorted(out, key=lambda sp: _M[sp])


def apply_offer(snap: Snap, offer: TradeOffer, alive) -> Snap:
    s = snap.copy()
    a, b = offer.proposer, offer.target
    for sp in offer.give_spaces:
        s.owner[sp] = b
    for sp in offer.get_spaces:
        s.owner[sp] = a
    s.cash[a] += offer.get_cash - offer.give_cash
    s.cash[b] += offer.give_cash - offer.get_cash
    s.cards[a] += offer.get_cards - offer.give_cards
    s.cards[b] += offer.give_cards - offer.get_cards
    fresh = set(s.fresh)
    for receiver, spaces in ((b, offer.give_spaces), (a, offer.get_spaces)):
        for sp in spaces:
            if s.mort[sp] and sp not in fresh:
                s.cash[receiver] -= _J[sp]
                fresh.add(sp)
    s.fresh = frozenset(fresh)

    for pid in (a, b):
        if s.cash[pid] < 0:
            order = list(mortgage_order(s, pid)) + [sp for sp in _core_unbuilt(s, pid) if sp not in mortgage_order(s, pid)]
            morts = _cover(order, s.cash[pid], 0)
            if morts is None:
                morts = order
            if morts:
                s = apply_mortgages(s, pid, morts)
    s.settle(alive)
    return s


@dataclass
class TradeEval:
    score: float
    delta_own: float
    delta_opp: float
    ok: bool
    reason: str = ""
    plan: Plan | None = None

    sense: float = 0.0

    partial: bool = False


class TradeSearch:
    """Compare both players' best post-trade plans, including possible debt settlement."""
    def __init__(self, ctx: Ctx, snap: Snap, me: int, our_turn: bool, rescue: bool = False):
        self.ctx, self.snap, self.me = ctx, snap, me
        self.our_turn = our_turn
        self.rescue = rescue
        self.P = ctx.params
        self.base_own = best_own(snap, ctx, me)
        self._agg_before: dict[int, tuple] = {}
        self._inherit: dict[int, float | None] = {}
        self._cache: dict = {}
        self.regions: dict = {}

    def agg_before(self, j: int) -> tuple:
        if j not in self._agg_before:
            s = aggressive(self.snap, self.ctx, j, self.me)
            be = board_eval(s, self.ctx)
            debt = s.debt_vector(self.ctx.n)
            self._agg_before[j] = (be.value(j, s.cash, debt, self.ctx), be.q(self.me, s.cash, debt))
        return self._agg_before[j]

    def inherit_value(self, j: int) -> float | None:
        if j in self._inherit:
            return self._inherit[j]
        s, ctx, me = self.snap, self.ctx, self.me
        owed_me = sum(a for d, c, a in s.debts if d == j and c == me)
        owed = s.debt_of(j)
        out = None
        if owed_me > 0:
            be = board_eval(s, ctx)
            if s.cash[j] + be.bank_extra[j] < owed:
                h = s.copy()
                cash_j = h.cash[j]
                fresh = set(h.fresh)
                for sp in range(40):
                    if h.owner[sp] != j:
                        continue
                    lv = h.bld[sp]
                    if lv:
                        cash_j += lv * (BOARD[sp].house_cost // 2)
                        if lv == HOTEL:
                            h.hotels += 1
                        else:
                            h.houses += lv
                        h.bld[sp] = 0
                    h.owner[sp] = me
                    if h.mort[sp]:
                        h.cash[me] -= _J[sp]
                        fresh.add(sp)
                h.cash[me] += cash_j
                h.cash[j] = 0
                h.cards[me] += h.cards[j]
                h.cards[j] = 0
                h.debts = [x for x in h.debts if x[0] != j]
                h.fresh = frozenset(fresh)
                dctx = ctx_without(ctx, j)
                out = best_own(h, dctx, me).v
        self._inherit[j] = out
        return out

    def evaluate(self, offer: TradeOffer, threshold: float | None = None) -> TradeEval:
        hit = self._cache.get(offer)
        if hit is not None and (not hit.partial or (threshold is not None and hit.delta_own < threshold)):
            return hit
        res = self._evaluate(offer, threshold)
        self._cache[offer] = res
        return res

    def _evaluate(self, offer: TradeOffer, threshold: float | None) -> TradeEval:
        ctx, me, P = self.ctx, self.me, self.P
        j = offer.target if offer.proposer == me else offer.proposer
        after = apply_offer(self.snap, offer, ctx.alive)
        own = best_own(after, ctx, me)
        base_v = self.base_own.v
        inh = self.inherit_value(j)
        if inh is not None:
            if after.debt_of(j) > 0:
                return TradeEval(-1e9, 0.0, 0.0, False, 'The debtor will go bankrupt owing me money anyway', own)
            base_v = inh
        delta_own = own.v - base_v


        sense = _nominal_gain(self.snap, offer, j)
        abe = board_eval(after, ctx)
        adebt = after.debt_vector(ctx.n)
        ok, reason = True, ""
        if not self.rescue and not own.offensive and not risk_ok(own.snap, ctx, me):
            pay = (offer.give_cash - offer.get_cash) if offer.proposer == me else (offer.get_cash - offer.give_cash)
            if pay > 0 or gap(own.snap, ctx, me) > gap(self.snap, ctx, me) + _EPS_GAP:
                ok, reason = False, 'violates reserve'
        if threshold is not None and (not ok or delta_own < threshold):
            return TradeEval(delta_own, delta_own, 0.0, False, reason or 'My gain is below the threshold', own, sense,
                             partial=True)
        agg = aggressive(after, ctx, j, me)
        be = board_eval(agg, ctx)
        debt = agg.debt_vector(ctx.n)
        vj_after = be.value(j, agg.cash, debt, ctx)
        q_after = be.q(me, agg.cash, debt)
        vj_before, q_before = self.agg_before(j)
        delta_opp = vj_after - vj_before
        score = delta_own - P.partner_gain_weight * max(0.0, delta_opp)
        if ok and not self.rescue and not ctx.endgame:
            new_full = [g for g in after.full_groups(j) if g not in self.snap.full_groups(j)]
            dangerous = [g for g in new_full if abe.can_three(j, g, after.cash, adebt)]
            if dangerous and q_after - q_before > P.rival_risk_threshold:
                mine_new = [g for g in after.full_groups(me) if g not in self.snap.full_groups(me)]
                ours_three = any(all(own.snap.bld[i] >= 3 for i in GROUPS[g]) or abe.can_three(me, g, after.cash, adebt)
                                 for g in mine_new)
                if not (mine_new and ours_three and self.our_turn and score > 0):
                    ok, reason = False, 'gives the rival a dangerous set they can develop'
        return TradeEval(score, delta_own, delta_opp, ok, reason, own, sense)


_EPS_GAP = 1e-6


def _field_nominal(snap: Snap, sp: int) -> float:
    return BOARD[sp].price - (_M[sp] + _J[sp] if snap.mort[sp] else 0)


def _nominal_gain(snap: Snap, offer: TradeOffer, j: int) -> float:
    if offer.proposer == j:
        gets, gives, cin, cout = offer.get_spaces, offer.give_spaces, offer.get_cash, offer.give_cash
    else:
        gets, gives, cin, cout = offer.give_spaces, offer.get_spaces, offer.give_cash, offer.get_cash
    return (sum(_field_nominal(snap, sp) for sp in gets) - sum(_field_nominal(snap, sp) for sp in gives)
            + cin - cout)


def risk_ok(snap: Snap, ctx: Ctx, me: int) -> bool:
    P = ctx.params
    be = board_eval(snap, ctx)
    debt = snap.debt_vector(ctx.n)
    r = be.reserve(me, snap.cash, debt, P)
    c = snap.cash[me] - debt[me]
    return c >= 0 and c + be.safe_mort[me] >= max(r, P.minimum_cash, P.cash_share * r)


def gap(snap: Snap, ctx: Ctx, me: int) -> float:
    be = board_eval(snap, ctx)
    debt = snap.debt_vector(ctx.n)
    r = be.reserve(me, snap.cash, debt, ctx.params)
    return max(0.0, r - (snap.cash[me] - debt[me] + be.safe_mort[me]))


def ctx_without(ctx: Ctx, j: int) -> Ctx:
    from .sol_sets_evaluation import branches

    c = Ctx.__new__(Ctx)
    c.__dict__.update(ctx.__dict__)
    alive = list(ctx.alive)
    alive[j] = False
    c.alive = tuple(alive)
    c.n_alive = sum(alive)
    others = c.n_alive - 1
    c.branches = tuple(
        branches(ctx.pos[i], ctx.jail[i][0], ctx.jail[i][1], ctx.doubles2 and i == ctx.current, ctx.held, others)
        if c.alive[i] else () for i in range(ctx.n))
    c._derive()
    c.key = (c.alive,) + ctx.key[1:]
    return c
