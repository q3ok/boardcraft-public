"""Choose acquisitions, financing and trades that support a developable set."""
import copy

from ..board import BOARD, BUYABLE, GROUPS, HOTEL, Kind
from ..actions import Action, ActionKind, Decision, Phase
from ..state import GameState
from ..buildings import apply_property_action, mortgage_actions, sell_actions
from ..text import seat_label
from . import Choice, describe_trade, variation
from ..trade import TradeOffer, can_propose, trade_proposer, validate_offer
from .safety import RESCUE_MARGIN, liquidation_after, owed_by, rescue_shortfall
from .sol_sets_evaluation import (
    _J, _M, DEFAULT_PARAMS, Ctx, Snap, SolSetsParams, board_eval, color_weight, full_group, full_groups,
    group_built, mortgage_class,
)
from .sol_sets_plans import (
    Plan, TradeSearch, apply_offer, best_own, evaluate, mortgage_order,
)
from .trading import liquidation_cash

SOL_SETS_ID = 'sol-sets'
SOL_SETS_NAME = 'Sol Sets'

ROLL = Action(ActionKind.ROLL)
END_TURN = Action(ActionKind.END_TURN)
PASS = Action(ActionKind.PASS)
BUY = Action(ActionKind.BUY)
DECLINE = Action(ActionKind.DECLINE)
PASS_BID = Action(ActionKind.PASS_BID)
BID1 = Action(ActionKind.BID, amount=1)
ACCEPT = Action(ActionKind.ACCEPT_TRADE)
REJECT = Action(ActionKind.REJECT_TRADE)
DECLARE = Action(ActionKind.DECLARE_BANKRUPTCY)
USE_CARD = Action(ActionKind.USE_JAIL_CARD)
PAY_FINE = Action(ActionKind.PAY_JAIL_FINE)

_EPS = 1e-6


def _names(state: GameState) -> list[str]:
    return [seat_label(i) for i in range(len(state.players))]


def _fork(state: GameState) -> GameState:
    s = copy.copy(state)
    s.owner = list(state.owner)
    s.buildings = list(state.buildings)
    s.mortgaged = list(state.mortgaged)
    s.players = [copy.copy(p) for p in state.players]
    return s


def _mk(me: int, j: int, give=(), get=(), c: int = 0, give_cards: int = 0, get_cards: int = 0) -> TradeOffer:
    return TradeOffer(me, j, give_spaces=tuple(give), get_spaces=tuple(get), give_cash=max(c, 0), get_cash=max(-c, 0),
                      give_cards=give_cards, get_cards=get_cards)


def _net(offer: TradeOffer, me: int) -> int:
    if offer.proposer == me:
        return offer.give_cash - offer.get_cash
    return offer.get_cash - offer.give_cash


def _sides(offer: TradeOffer, me: int):
    if offer.proposer == me:
        return offer.target, offer.give_spaces, offer.get_spaces, offer.give_cards, offer.get_cards
    return offer.proposer, offer.get_spaces, offer.give_spaces, offer.get_cards, offer.give_cards


class SolSetsBot:
    def __init__(self):
        self.label = SOL_SETS_ID
        self.params: SolSetsParams = DEFAULT_PARAMS

        self._auction: dict = {}
        self._neg: dict = {}
        self._mort_round: dict[int, int] = {}
        self._slot = None
        self._slot_steps = 0
        self._round = 0
        self._buying: tuple | None = None
        self._rescue_tried: set = set()
        self._ts_cache: dict = {}

        self._variation_rng = variation.new_rng(0)

    def reseed(self, seed: int) -> None:
        self._variation_rng = variation.new_rng(seed)

    def choose(self, state: GameState, decision: Decision) -> Choice:
        if decision.phase is not Phase.AUCTION:
            self._auction.clear()
        eps = self.params.random_move_probability
        if eps > 0 and self._variation_rng.random() < eps:
            a = variation.random_action(state, decision, self._variation_rng, lambda a: a.kind is ActionKind.ACCEPT_TRADE)
            if a is not None:
                return Choice(a, f'Random move (random move probability {eps:g})')
        try:
            return self._decide(state, decision)
        except Exception:
            return Choice(self._fallback(decision), 'Use the available fallback move.')

    @staticmethod
    def _fallback(d: Decision) -> Action:
        acts = d.actions
        if d.phase is Phase.RAISE_FUNDS:
            for a in acts:
                if a.kind in (ActionKind.MORTGAGE, ActionKind.SELL_BUILDING):
                    return a
        for a in (REJECT, PASS_BID, PASS, ROLL, END_TURN, DECLINE, DECLARE):
            if a in acts:
                return a
        return acts[0]

    def _decide(self, state: GameState, d: Decision) -> Choice:
        self._round = state.round
        ctx = Ctx(state, self.params)
        snap = Snap.from_state(state)
        slot = (state.turn, d.phase, state.window.serial if state.window is not None else -1, d.player,
                len(state.debts), len(state.pending_takeovers))
        if slot != self._slot:
            self._slot, self._slot_steps = slot, 0
        ph = d.phase
        if ph is Phase.RAISE_FUNDS:
            return self._debt(state, d, ctx, snap)
        if ph is Phase.TRADE_RESPONSE:
            return self._respond(state, d, ctx, snap)
        if ph is Phase.MORTGAGE_TAKEOVER:
            return self._takeover(state, d, ctx, snap)
        if ph is Phase.AUCTION:
            return self._auction_move(state, d, ctx, snap)
        if ph is Phase.BUY_DECISION:
            return self._buy(state, d, ctx, snap)
        if ph is Phase.TRADE_WINDOW:
            return self._window(state, d, ctx, snap)
        return self._turn(state, d, ctx, snap)

    def _step(self, c: Choice) -> Choice:
        self._slot_steps += 1
        if c.action.kind is ActionKind.MORTGAGE:
            self._mort_round[c.action.space] = self._round
        return c

    def _turn(self, state: GameState, d: Decision, ctx: Ctx, snap: Snap) -> Choice:
        me = d.player
        self._round = state.round
        if d.phase is Phase.POST_ROLL:
            c = self._manage(state, d, ctx, snap)
            if c is not None:
                return c
        offer = self._proposal(state, d, ctx, snap)
        if offer is not None:
            return offer
        if d.phase is Phase.PRE_ROLL and state.players[me].in_jail:
            c = self._jail(state, d, ctx, snap)
            if c is not None:
                return c
        if ROLL in d.actions:
            return Choice(ROLL, 'Roll. No profitable action before the roll')
        return Choice(END_TURN, 'End my turn')

    def _window(self, state: GameState, d: Decision, ctx: Ctx, snap: Snap) -> Choice:
        self._round = state.round
        c = self._manage(state, d, ctx, snap)
        if c is not None:
            return Choice(c.action, f'Trade window: {c.explanation}')
        offer = self._proposal(state, d, ctx, snap)
        if offer is not None:
            return Choice(offer.action, f'Trade window: {offer.explanation}')
        return Choice(PASS, 'Pass. No beneficial action in this trade window')

    def _manage(self, state: GameState, d: Decision, ctx: Ctx, snap: Snap) -> Choice | None:
        P = self.params
        me = d.player
        if self._slot_steps >= P.management_steps:
            return None
        acts = set(d.actions)
        if not any(a.kind in (ActionKind.BUILD, ActionKind.UNMORTGAGE, ActionKind.MORTGAGE) for a in acts):
            return None
        v0, _q0 = evaluate(snap, ctx, me)
        plan = best_own(snap, ctx, me)
        if plan.steps and plan.v > v0 + _EPS:
            a, why = self._first_step(state, plan, ctx, snap, me)
            if a is not None and a in acts:
                tag = ' (offensive exception)' if plan.offensive else ""
                return self._step(Choice(a, f'{why}. Management plan gains {plan.v - v0:.0f} points{tag}'))
        return self._unmortgage(state, d, ctx, snap, v0)

    def _first_step(self, state: GameState, plan: Plan, ctx: Ctx, snap: Snap, me: int):
        for st in plan.steps:
            if st[0] == "M":
                if not snap.mort[st[1]]:
                    return Action(ActionKind.MORTGAGE, space=st[1]), f'Mortgage {BOARD[st[1]].name} to finance construction'
                continue
            _b, g, lv = st
            sp = GROUPS[g]
            for i in sp:
                if snap.mort[i]:
                    return Action(ActionKind.UNMORTGAGE, space=i), f'Redeem {BOARD[i].name}. Unlocks development of the group'
            levels = [snap.bld[i] for i in sp]
            low = min(levels)
            cand = [i for i, t in zip(sp, lv) if snap.bld[i] < t and snap.bld[i] == low]
            if not cand:
                continue
            i = self._build_order(cand, ctx, snap, me)
            lvl = snap.bld[i] + 1
            what = "hotel" if lvl == HOTEL else f'{lvl}houses'
            target = "/".join("H" if t == HOTEL else str(t) for t in lv)
            return Action(ActionKind.BUILD, space=i), f'Build {what} on {BOARD[i].name} (package {target})'
        return None, ""

    def _build_order(self, cand: list[int], ctx: Ctx, snap: Snap, me: int) -> int:
        be = board_eval(snap, ctx)
        debt = snap.debt_vector(ctx.n)
        nxt = ctx.next_roller(me)
        thr = snap.cash[nxt] + be.bank_extra[nxt] - debt[nxt] if nxt is not None else None
        pl = be.p_land[nxt] if nxt is not None and be.p_land[nxt] else {}

        def key(i):
            s = BOARD[i]
            lv = snap.bld[i]
            old = s.rents[lv] if lv else s.rents[0] * 2
            new = s.rents[lv + 1]
            cross = thr is not None and old <= thr < new
            return (not cross, -pl.get(i, 0.0), -(new - old) / s.house_cost, -new, i)

        return min(cand, key=key)

    def _unmortgage(self, state: GameState, d: Decision, ctx: Ctx, snap: Snap, v0: float) -> Choice | None:
        P = self.params
        me = d.player
        best = None
        for a in d.actions:
            if a.kind is not ActionKind.UNMORTGAGE or a.space in snap.fresh:
                continue
            sp = a.space
            if self._mort_round.get(sp, -10) >= state.round - 1:
                continue
            s = snap.copy()
            s.mort[sp] = False
            s.cash[me] -= s.unmortgage_price(sp)
            be = board_eval(s, ctx)
            debt = s.debt_vector(ctx.n)
            r = be.reserve(me, s.cash, debt, P)
            buf = r + max(P.minimum_buffer, P.buffer_share * r)
            if s.cash[me] - debt[me] < max(P.minimum_cash, P.cash_share * r) or s.cash[me] - debt[me] + be.safe_mort[me] < buf:
                continue
            v = be.value(me, s.cash, debt, ctx)
            if v > v0 + _EPS and (best is None or v > best[0]):
                best = (v, a)
        if best is None:
            return None
        return self._step(Choice(best[1], f'Repay the mortgage on {BOARD[best[1].space].name}. Restores income and preserves my cash buffer'))

    def _jail(self, state: GameState, d: Decision, ctx: Ctx, snap: Snap) -> Choice | None:
        P = self.params
        me = d.player
        be = board_eval(snap, ctx)
        debt = snap.debt_vector(ctx.n)
        free = sum(1 for t in BUYABLE if snap.owner[t] is None)
        early = free >= P.acquisition_unowned and not be.engine[me]
        if early and not be.any_threat(me, snap.cash, debt):
            if USE_CARD in d.actions:
                return Choice(USE_CARD, f'Use a jail release card. Unowned properties: {free}, rivals have no dangerous buildings')
            if PAY_FINE in d.actions:
                r = be.reserve(me, snap.cash, debt, P)
                after = snap.cash[me] - 50 - debt[me]
                if after >= P.bail_cash_floor or after + be.safe_mort[me] >= r:
                    return Choice(PAY_FINE, f'Pay the jail fine. Unowned properties: {free}, remaining cash after bail ${after}')
            return None
        why = 'my developed set is generating income' if be.engine[me] else 'rivals charge dangerous rents' if be.any_threat(me, snap.cash, debt) else 'few unowned properties remain'
        return Choice(ROLL, f'Stay in jail and try for doubles. {why}')

    def _targets(self, ctx: Ctx, snap: Snap, me: int) -> list[str]:
        P = self.params
        be = board_eval(snap, ctx)
        debt = snap.debt_vector(ctx.n)
        funds = snap.cash[me] - debt[me] + be.safe_mort[me]
        rows = []
        for g, sp in GROUPS.items():
            mine = [i for i in sp if snap.owner[i] == me]
            if not mine:
                continue
            missing = [i for i in sp if snap.owner[i] != me]
            hc = BOARD[sp[0]].house_cost
            acq = 0.0
            for i in missing:
                o = snap.owner[i]
                f = BOARD[i].price
                acq += f if o is None else f * (P.last_property_estimate if len(missing) == 1 else P.rival_property_estimate)
            fees = sum(_J[i] for i in missing if snap.mort[i]) + sum(snap.unmortgage_price(i) for i in sp if snap.mort[i])
            level = 5 if g == "brown" else 3
            build = sum(max(0, level - snap.bld[i]) for i in sp) * hc
            houses = level * len(sp) if level < 5 else 0
            hotels = len(sp) if level == 5 else 0
            res = max(P.developed_reserve, 40 * houses + 115 * hotels)

            own_m = sum(_M[i] for i in mine if not snap.mort[i] and not full_group(snap.owner, me, g))
            cost = acq + fees + build + res
            fin = cost <= funds - own_m + res
            fin2 = acq + fees + sum(max(0, 2 - snap.bld[i]) for i in sp) * hc + P.developed_reserve <= funds - own_m + P.developed_reserve
            if not missing and fin:
                cls = 0
            elif not missing and fin2:
                cls = 1
            elif len(missing) == 1 and fin:
                cls = 2
            else:
                cls = 3
            rents = sum(BOARD[i].rents[level] for i in sp)
            ratio = color_weight(P, g) * rents / max(1.0, cost - res + res)
            rows.append((cls, -ratio, len(missing), cost, g))
        rows.sort()
        return [r[-1] for r in rows]

    def _limit(self, state: GameState, ctx: Ctx, snap: Snap, me: int, space: int) -> tuple[int, str, bool]:
        P = self.params
        s = BOARD[space]
        F = s.price
        g = s.group
        owner = snap.owner
        closing = g is not None and all(owner[i] == me for i in GROUPS[g] if i != space)
        blocker = False
        if g is not None:
            others = [owner[i] for i in GROUPS[g] if i != space]
            blocker = others[0] is not None and others[0] != me and all(o == others[0] for o in others)
        same = (g is not None and any(owner[i] == me for i in GROUPS[g])) or (
            s.kind is Kind.RAILROAD and any(owner[i] == me for i in (5, 15, 25, 35))) or (
            s.kind is Kind.UTILITY and any(owner[i] == me for i in (12, 28)))
        targets = self._targets(ctx, snap, me)[:2]

        s1 = snap.copy()
        s1.owner[space] = me
        base_v = best_own(snap, ctx, me).v
        be1 = board_eval(s1, ctx)
        debt = s1.debt_vector(ctx.n)
        r1 = be1.reserve(me, s1.cash, debt, P)
        fin = s1.cash[me] - debt[me] + be1.safe_mort[me] - r1

        basket = sum(BOARD[x].price for x in state.pending_auctions if x != space and BOARD[x].group is not None
                     and any(owner[i] == me for i in GROUPS[BOARD[x].group]))
        fin -= basket
        plan = closing or blocker or same or g in targets
        acq = (sum(1 for t in BUYABLE if owner[t] is None) >= P.acquisition_unowned and not be1.engine[me]
               and snap.cash[me] - debt[me] - F >= r1 + max(P.minimum_buffer, P.buffer_share * r1))
        if closing:
            strategic = max(F, self._breakeven(s1, ctx, me, base_v, int(P.key_search_factor * F)))
            why = 'completes my color group'
        elif plan:
            strategic = F
            why = ("last property blocking a rival's set" if blocker else 'part of my color group' if same
                   else 'primary or secondary target')
        elif acq and F <= P.cheap_property:
            strategic = F
            why = 'affordable early property with ample cash remaining'
        elif acq:
            strategic = min(F, self._breakeven(s1, ctx, me, base_v, F))
            why = 'early purchase supported by position evaluation'
            plan = strategic >= F
        else:
            rival = g is not None and any(o is not None and o != me for o in (owner[i] for i in GROUPS[g]))
            strategic = int(P.auction_block_factor * F) if rival else s.mortgage_value
            why = 'non-core property for blocking or trading' if rival else 'non-core property outside the plan'
        limit = int(max(0, min(strategic, fin)))
        return limit, why, plan

    def _breakeven(self, s1: Snap, ctx: Ctx, me: int, base_v: float, cap: int) -> int:
        def ok(p):
            s = s1.copy()
            s.cash[me] -= p
            return best_own(s, ctx, me).v >= base_v

        if not ok(0):
            return 0
        lo, hi = 0, max(1, cap)
        if ok(hi):
            return hi
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if ok(mid):
                lo = mid
            else:
                hi = mid
        return lo

    def _buy(self, state: GameState, d: Decision, ctx: Ctx, snap: Snap) -> Choice:
        me = d.player
        space = state.pending_buy
        F = BOARD[space].price
        self._round = state.round
        if self._buying == (state.turn, space):
            limit, why, plan = F, 'Continue financing the purchase', True
        else:
            limit, why, plan = self._limit(state, ctx, snap, me, space)
        if limit < F:
            return Choice(DECLINE, f'Decline to buy {BOARD[space].name}. Limit ${limit} < price ${F} ({why}). Bidding with this limit')
        if BUY in d.actions:
            self._buying = None
            return Choice(BUY, f'Buy {BOARD[space].name} for ${F}: {why} (limit ${limit})')

        need = F - snap.cash[me]
        for sp in mortgage_order(snap, me):
            a = Action(ActionKind.MORTGAGE, space=sp)
            if a in d.actions and self._slot_steps < self.params.management_steps:
                total = sum(_M[x] for x in mortgage_order(snap, me))
                if total < need:
                    break
                self._buying = (state.turn, space)
                return self._step(Choice(a, f'Mortgage {BOARD[sp].name} to buy {BOARD[space].name} ({why})'))
        return Choice(DECLINE, f'Decline. Insufficient cash for {BOARD[space].name}')

    def _auction_move(self, state: GameState, d: Decision, ctx: Ctx, snap: Snap) -> Choice:
        me = d.player
        a = state.auction
        key = (state.turn, a.space)
        if key not in self._auction:
            limit, why, _plan = self._limit(state, ctx, snap, me, a.space)
            self._auction[key] = (limit, why)
        limit, why = self._auction[key]
        if a.high_bid + 1 <= limit and BID1 in d.actions:
            return Choice(BID1, f'Raise by $1 to ${a.high_bid + 1}: {why} (limit ${limit})')
        return Choice(PASS_BID, f'Pass. Price above limit ${limit} ({why})')

    def _takeover(self, state: GameState, d: Decision, ctx: Ctx, snap: Snap) -> Choice:
        P = self.params
        me, sp = state.pending_takeovers[0]
        payoff = Action(ActionKind.UNMORTGAGE, space=sp)
        name = BOARD[sp].name
        pend = [x for p, x in state.pending_takeovers if p == me]
        fees = sum(_J[x] for x in pend)
        if payoff not in d.actions:
            return Choice(PASS, f'Pay only interest on {name}. Insufficient cash for full repayment (package interest ${fees})')

        b = self._drop_fee(snap.copy(), me, sp)
        b.cash[me] -= _J[sp]
        b.fresh = frozenset(x for x in b.fresh if x != sp)
        a = self._drop_fee(snap.copy(), me, sp)
        a.cash[me] -= _M[sp] + _J[sp]
        a.mort[sp] = False
        a.fresh = frozenset(x for x in a.fresh if x != sp)
        pa = best_own(a, ctx, me)
        pb = best_own(b, ctx, me)
        be = board_eval(a, ctx)
        debt = a.debt_vector(ctx.n)
        r = be.reserve(me, a.cash, debt, P)
        safe = a.cash[me] - debt[me] >= max(P.minimum_cash, P.cash_share * r)
        if safe and pa.v > pb.v + _EPS:
            return Choice(payoff, f'Immediately repay the mortgage on {name}. Better plan (+{pa.v - pb.v:.0f} points), reserving fees for the remaining transfers (${fees - _J[sp]})')
        return Choice(PASS, f'Pay only interest on {name} (${_J[sp]}), full repayment is too costly')

    @staticmethod
    def _drop_fee(s: Snap, me: int, sp: int) -> Snap:
        for k, (dd, c, amt) in enumerate(s.debts):
            if dd == me and c is None and amt == _J[sp]:
                s.debts = s.debts[:k] + s.debts[k + 1:]
                break
        return s

    def _search(self, ctx: Ctx, snap: Snap, me: int, our_turn: bool) -> TradeSearch:
        """Reuse pure position evaluations across identical negotiation windows."""
        key = (ctx.key, snap.board_key(), tuple(snap.cash), tuple(snap.debts), snap.houses, snap.hotels, snap.fresh,
               me, our_turn, self.params)
        ts = self._ts_cache.get(key)
        if ts is None:
            if len(self._ts_cache) >= 8:
                self._ts_cache.pop(next(iter(self._ts_cache)))
            ts = self._ts_cache[key] = TradeSearch(ctx, snap, me, our_turn)
        return ts

    def _sig(self, snap: Snap, me: int, j: int) -> tuple:
        mine = tuple(i for i in range(40) if snap.owner[i] == me)
        theirs = tuple(i for i in range(40) if snap.owner[i] == j)
        return (mine, theirs, tuple(sorted(snap.full_groups(me))), tuple(sorted(snap.full_groups(j))),
                snap.debt_of(me) > 0, snap.debt_of(j) > 0)

    def _attempts(self, key, snap: Snap, me: int, j: int) -> tuple[int, set]:
        """Restart an offer sequence only after assets, debts or available cash change."""
        sig = self._sig(snap, me, j)
        rec = self._neg.get(key)
        P = self.params
        if (rec is None or rec[0] != sig or abs(rec[1] - snap.cash[me]) >= P.retry_cash_change
                or abs(rec[2] - snap.cash[j]) >= P.retry_cash_change):
            rec = (sig, snap.cash[me], snap.cash[j], 0, set())
            self._neg[key] = rec
        return rec[3], rec[4]

    def _note_attempt(self, key, offer: TradeOffer) -> None:
        sig, cm, cj, k, sent = self._neg[key]
        self._neg[key] = (sig, cm, cj, k + 1, sent | {offer})

    def _skeletons(self, ctx: Ctx, snap: Snap, me: int, j: int, debt_mode: bool = False) -> list:
        P = self.params
        owner, bld = snap.owner, snap.bld

        def tradable(sp):
            g = BOARD[sp].group
            return not (g is not None and group_built(bld, g))

        mine_full = set(full_groups(owner, me))
        give = [sp for sp in range(40) if owner[sp] == me and tradable(sp) and BOARD[sp].group not in mine_full]
        if debt_mode:
            give = [sp for sp in give if mortgage_class(owner, bld, snap.mort, me, sp) <= 4]
        theirs = [sp for sp in range(40) if owner[sp] == j and tradable(sp)]
        want = []
        for f in theirs:
            g = BOARD[f].group
            if g is None:
                continue
            if any(owner[i] == me for i in GROUPS[g]):
                want.append(f)
                continue

            others = [owner[i] for i in GROUPS[g] if i != f]
            k = others[0]
            if k is not None and k not in (me, j) and all(o == k for o in others):
                want.append(f)

        def completes(pid, spaces_after):
            out = set()
            for g, sp in GROUPS.items():
                if all(i in spaces_after for i in sp):
                    out.add(g)
            return out

        my_set = {i for i in range(40) if owner[i] == me}
        j_set = {i for i in range(40) if owner[i] == j}

        def pre(gv, gt):
            after_me = (my_set - set(gv)) | set(gt)
            after_j = (j_set - set(gt)) | set(gv)
            new_me = completes(me, after_me) - completes(me, my_set)
            new_j = completes(j, after_j) - completes(j, j_set)
            score = 0.0
            for f in gt:
                g = BOARD[f].group
                score += BOARD[f].price * (color_weight(P, g) if g else 0.5)
            for g in new_me:
                score += 3 * color_weight(P, g) * sum(BOARD[i].price for i in GROUPS[g])
            for s in gv:
                score -= BOARD[s].price
            for g in new_j:
                score -= 2 * color_weight(P, g) * sum(BOARD[i].price for i in GROUPS[g])
            return score

        sk = set()
        for f in want:
            sk.add(((), (f,)))
            for s in give:
                if BOARD[s].group != BOARD[f].group or BOARD[s].group is None:
                    sk.add(((s,), (f,)))
            for a_ in range(len(give)):
                for b_ in range(a_ + 1, len(give)):
                    s1, s2 = give[a_], give[b_]
                    if BOARD[f].group not in (BOARD[s1].group, BOARD[s2].group):
                        sk.add(((s1, s2), (f,)))
        for a_ in range(len(want)):
            for b_ in range(a_ + 1, len(want)):
                f1, f2 = want[a_], want[b_]
                if BOARD[f1].group == BOARD[f2].group:
                    sk.add(((), (f1, f2)))
                    for s in give:
                        if BOARD[s].group != BOARD[f1].group:
                            sk.add(((s,), (f1, f2)))

        for s in give:
            g = BOARD[s].group
            if g is not None:
                interest = any(owner[i] == j for i in GROUPS[g])
            else:
                same = (5, 15, 25, 35) if BOARD[s].kind is Kind.RAILROAD else (12, 28)
                interest = any(owner[i] == j for i in same)
            if interest or debt_mode:
                sk.add(((s,), ()))
        rows = sorted(((-pre(gv, gt), gv, gt) for gv, gt in sk), key=lambda t: (t[0], t[1], t[2]))
        return [(gv, gt) for _s, gv, gt in rows[: max(0, P.offer_skeletons)]]

    def _bound(self, snap: Snap, me: int, j: int, spaces, receiver: int) -> float:
        P = self.params
        owner = snap.owner
        tot = 0.0
        after = set(i for i in range(40) if owner[i] == receiver) | set(spaces)
        for f in spaces:
            g = BOARD[f].group
            key = g is not None and all(i in after for i in GROUPS[g])
            tot += BOARD[f].price * (P.key_search_factor if key else P.ordinary_search_factor)
        return tot

    def _region(self, ts: TradeSearch, state: GameState, snap: Snap, me: int, j: int, give, get, threshold: float,
                extra=(), cards=(0, 0)):
        rkey = (j, tuple(give), tuple(get), threshold, tuple(extra), tuple(cards))
        if rkey in ts.regions:
            return ts.regions[rkey]
        res = ts.regions[rkey] = self._region_calc(ts, state, snap, me, j, give, get, threshold, extra, cards)
        return res

    def _region_calc(self, ts, state, snap, me, j, give, get, threshold, extra, cards):
        P = self.params
        grid = max(1, P.cash_grid)
        hi = min(snap.cash[me], int(self._bound(snap, me, j, get, me))) if get else 0
        j_max = state.players[j].cash
        lo = -min(j_max, int(self._bound(snap, me, j, give, j))) if give else 0
        if not give and not get:
            return None
        pts = set(range((lo // grid) * grid, hi + 1, grid)) | {0, hi, lo} | {int(x) for x in extra}
        pts = sorted(c for c in pts if lo <= c <= hi)
        feas = []
        evals = {}
        for c in pts:
            offer = _mk(me, j, give, get, c, *cards)
            if validate_offer(state, offer) is not None or offer.is_empty():
                continue
            ev = ts.evaluate(offer, threshold)
            evals[c] = ev
            if ev.ok and ev.score >= threshold:
                feas.append(c)
        if not feas:
            return None

        one_sided = not give or not get
        sensible = [c for c in feas if evals[c].sense >= 0 and not (one_sided and c == 0 and not any(cards))]
        if not sensible:
            return None
        return min(sensible), max(feas), evals

    def _price_for(self, anchor: int, pmax: int, k: int, last: bool, feas_pts) -> int | None:
        P = self.params
        if k >= 3:
            return None
        if last or k >= 2:
            target = pmax
        elif k == 1:
            target = anchor + P.concession * (pmax - anchor)
        else:
            target = anchor
        ok = [c for c in feas_pts if anchor <= c <= target]
        return max(ok) if ok else anchor

    def _proposal(self, state: GameState, d: Decision, ctx: Ctx, snap: Snap) -> Choice | None:
        me = d.player
        if trade_proposer(state) != me or not can_propose(state):
            return None
        P = self.params
        our_turn = state.current == me and state.window is None
        ts = self._search(ctx, snap, me, our_turn)
        cands = []
        for j in range(ctx.n):
            if j == me or not ctx.alive[j]:
                continue
            for gv, gt in self._skeletons(ctx, snap, me, j):
                key = (j, gv, gt)
                k, sent = self._attempts(key, snap, me, j)
                if k >= 3:
                    continue
                reg = self._region(ts, state, snap, me, j, gv, gt, P.offer_gain_threshold)
                if reg is None:
                    continue
                anchor, pmax, evals = reg
                feas_pts = sorted(c for c, ev in evals.items() if ev.ok and ev.score >= P.offer_gain_threshold)
                c = self._price_for(anchor, pmax, k, False, feas_pts)
                if c is None:
                    continue
                offer = _mk(me, j, gv, gt, c)
                if offer in sent:
                    later = [x for x in feas_pts if x > c and _mk(me, j, gv, gt, x) not in sent]
                    if not later:
                        continue
                    c = later[0]
                    offer = _mk(me, j, gv, gt, c)
                ev = evals[c]
                cands.append((-ev.score, abs(c), key, offer, evals))
        if not cands:
            return None
        cands.sort(key=lambda t: t[:3])

        z = P.trade_variation
        _s, _c, key, offer, evals = cands[variation.pick(self._variation_rng, len(cands), z)]
        if z > 0:
            def fine(o, evals=evals, me=me):
                e = evals.get(_net(o, me))
                return e is not None and e.ok and e.score >= P.offer_gain_threshold
            offer = variation.varied(state, self._variation_rng, offer, z, fine)
        self._note_attempt(key, offer)
        return Choice(Action(ActionKind.PROPOSE_TRADE, trade=offer), describe_trade(offer, _names(state)))

    def _respond(self, state: GameState, d: Decision, ctx: Ctx, snap: Snap) -> Choice:
        P = self.params
        me = d.player
        self._round = state.round
        offer = state.trade.offer
        rescue = self._rescue_response(state, d)
        if rescue is not None:
            return rescue
        j, give, get, gcards, rcards = _sides(offer, me)
        in_debt = snap.debt_of(me) > 0
        threshold = 0 if in_debt else P.offer_gain_threshold
        if P.trade_variation > 0:
            threshold = max(0.0, threshold + variation.threshold(self._variation_rng, offer, P.trade_variation))
        our_turn = state.current == me and state.window is None
        ts = self._search(ctx, snap, me, our_turn)
        ev = ts.evaluate(offer)
        names = _names(state)
        if ev.ok and ev.score >= threshold:
            if ACCEPT in d.actions:
                return Choice(ACCEPT, f"Accept. My gain is {ev.delta_own:.0f}, rival's gain {ev.delta_opp:.0f}, score {ev.score:.0f}")

            pay = _net(offer, me)
            if pay > snap.cash[me] and self._slot_steps < P.management_steps:
                order = [sp for sp in mortgage_order(snap, me) if sp not in give]
                if snap.cash[me] + sum(_M[x] for x in order) >= pay:
                    sp = order[0]
                    a = Action(ActionKind.MORTGAGE, space=sp)
                    if a in d.actions:
                        return self._step(Choice(a, f'Mortgage {BOARD[sp].name} to accept a favorable trade'))

        if state.trade.exchanges < state.config.max_trade_exchanges:
            mine_before = [o for o in state.trade.history if o.proposer == me]
            k = len(mine_before)
            last = state.trade.exchanges + 1 >= state.config.max_trade_exchanges
            cur = _net(offer, me)
            reg = self._region(ts, state, snap, me, j, give, get, threshold, extra=(cur,), cards=(gcards, rcards))
            if reg is not None:
                anchor, pmax, evals = reg
                feas_pts = sorted(c for c, e in evals.items() if e.ok and e.score >= threshold)
                c = self._price_for(anchor, pmax, k, last, feas_pts)
                if c is not None and c < cur:
                    counter = _mk(me, j, give, get, c, gcards, rcards)
                    prev = {_net(o, me) for o in mine_before}
                    if c not in prev and validate_offer(state, counter) is None:
                        return Choice(Action(ActionKind.COUNTER_TRADE, trade=counter),
                                      f'Counteroffer: {describe_trade(counter, names)}')
        why = ev.reason or f'score {ev.score:.0f} below the threshold of {threshold}'
        return Choice(REJECT, f"Reject. {why} (my gain {ev.delta_own:.0f}, rival's gain {ev.delta_opp:.0f})")

    def _rescue_response(self, state: GameState, d: Decision) -> Choice | None:
        me = d.player
        short = rescue_shortfall(state, me)
        if short <= 0:
            return None
        offer = state.trade.offer
        owed = owed_by(state, me)
        after = liquidation_after(state, offer, me)
        if after >= owed and ACCEPT in d.actions:
            return Choice(ACCEPT, f'Accept to avoid bankruptcy (shortfall ${short}), and this trade covers the debt')
        j, give, get, gc, rc = _sides(offer, me)
        net_in = -_net(offer, me) + int(owed - after + RESCUE_MARGIN + 9) // 10 * 10
        asked = [-_net(o, me) for o in state.trade.history if o.proposer == me]
        if asked:
            net_in = min(net_in, min(asked))
        if net_in > -_net(offer, me) and state.trade.exchanges < state.config.max_trade_exchanges:
            counter = _mk(me, j, give, get, -net_in, gc, rc)
            if validate_offer(state, counter) is None and state.players[j].cash >= counter.get_cash:
                return Choice(Action(ActionKind.COUNTER_TRADE, trade=counter),
                              f'Rescue counteroffer. Request the shortfall (${short})')
        if after > liquidation_cash(state, me) and ACCEPT in d.actions:
            return Choice(ACCEPT, f'Accept to reduce the bankruptcy shortfall (${short})')
        return None

    def _debt(self, state: GameState, d: Decision, ctx: Ctx, snap: Snap) -> Choice:
        P = self.params
        me = d.player
        self._round = state.round
        owed = owed_by(state, me)
        cash = state.players[me].cash
        liq = liquidation_cash(state, me)
        acts = [a for a in d.actions if a.kind in (ActionKind.MORTGAGE, ActionKind.SELL_BUILDING)]
        if liq < owed:
            offer = self._rescue_offer(state, ctx, snap, me, owed, liq)
            if offer is not None:
                return Choice(Action(ActionKind.PROPOSE_TRADE, trade=offer),
                              f'Bank financing falls short of the debt by ${owed} (I can raise ${liq}), rescue offer: {describe_trade(offer, _names(state))}')
            if DECLARE in d.actions:
                return Choice(DECLARE, 'Bank funds and attempted sales are exhausted. Declare bankruptcy')
            a = self._liquidation_step(state, me, owed, acts)
            return Choice(a, 'Bank financing is insufficient. Liquidate before bankruptcy')
        side = [a for a in acts if a.kind is ActionKind.MORTGAGE
                and mortgage_class(snap.owner, snap.bld, snap.mort, me, a.space) <= 4]
        if cash + sum(_M[a.space] for a in side) >= owed:
            a = self._liquidation_step(state, me, owed, side)
            return Choice(a, f'Debt ${owed}, I have ${cash}, by mortgaging a non-core property, {BOARD[a.space].name} (debt repayment)')

        sale = self._debt_sale(state, ctx, snap, me, owed)
        if sale is not None:
            return sale
        a = self._liquidation_step(state, me, owed, acts)
        where = BOARD[a.space].name if a.space is not None else 'color group'
        what = 'Sell a building on' if a.kind is ActionKind.SELL_BUILDING else 'Mortgage'
        return Choice(a, f'Debt ${owed}: {what} {where}, the lowest strategic cost per dollar raised')

    def _liquidation_step(self, state: GameState, me: int, owed: int, allowed: list[Action]) -> Action:
        if len(allowed) == 1:
            return allowed[0]
        ctx = Ctx(state, self.params)

        def strat(st):
            s = Snap.from_state(st)
            be = board_eval(s, ctx)
            return be.strategic(me, s.cash, s.debt_vector(ctx.n), ctx)

        s0 = strat(state)
        cash0 = state.players[me].cash
        allowed_set = set(allowed)
        only_side = all(a.kind is ActionKind.MORTGAGE for a in allowed)
        beam = [(0.0, (), state)]
        covering = []
        for depth in range(3):
            cand = []
            for cost0, seq, st in beam:
                acts = mortgage_actions(st, me) + sell_actions(st, me)
                if depth == 0:
                    acts = [a for a in acts if a in allowed_set]
                elif only_side:
                    acts = [a for a in acts if a.kind is ActionKind.MORTGAGE
                            and mortgage_class(st.owner, st.buildings, st.mortgaged, me, a.space) <= 4]
                for a in acts:
                    st2 = _fork(st)
                    before = st2.players[me].cash
                    apply_property_action(st2, a)
                    got = st2.players[me].cash - before
                    loss = (BOARD[a.space].mortgage_value // 10 if a.kind is ActionKind.MORTGAGE else got)
                    item = (cost0 + loss, seq + (a,), st2)
                    if st2.players[me].cash >= owed:
                        covering.append(item)
                    else:
                        cand.append(item)
            if covering:
                break
            scored = []
            for loss, seq, st2 in cand:
                c = loss + s0 - strat(st2)
                scored.append((c / max(1, st2.players[me].cash - cash0), seq[0].space if seq[0].space is not None else -1,
                               loss, seq, st2))
            scored.sort(key=lambda t: t[:2])
            beam = [(t[2], t[3], t[4]) for t in scored[:8]]
            if not beam:
                break
        if covering:
            best = min(((loss + s0 - strat(st2), len(seq), seq[0].space if seq[0].space is not None else -1, seq)
                        for loss, seq, st2 in covering), key=lambda t: t[:3])
            return best[3][0]
        if beam:
            return beam[0][1][0]
        return allowed[0]

    def _debt_sale(self, state: GameState, ctx: Ctx, snap: Snap, me: int, owed: int) -> Choice | None:
        if trade_proposer(state) != me or not can_propose(state):
            return None
        cash = snap.cash[me]
        ts = self._search(ctx, snap, me, False)
        best = None
        for j in range(ctx.n):
            if j == me or not ctx.alive[j]:
                continue
            for gv, gt in self._skeletons(ctx, snap, me, j, debt_mode=True):
                if gt:
                    continue
                rest = sum(_M[x] for x in mortgage_order(snap, me) if x not in gv)
                need = owed - cash - rest
                key = (j, gv, gt)
                k, sent = self._attempts(key, snap, me, j)
                if k >= 3:
                    continue
                reg = self._region(ts, state, snap, me, j, gv, gt, 0, extra=(-need,))
                if reg is None:
                    continue
                anchor, pmax, evals = reg
                ok = sorted(c for c, e in evals.items() if e.ok and e.score >= 0 and -c >= need and e.sense >= 0)
                if not ok:
                    continue
                c = ok[0] if k == 0 else ok[-1]
                offer = _mk(me, j, gv, gt, c)
                if offer in sent:
                    continue
                cand = (evals[c].score, key, offer)
                if best is None or cand[0] > best[0]:
                    best = cand
        if best is None:
            return None
        self._note_attempt(best[1], best[2])
        return Choice(Action(ActionKind.PROPOSE_TRADE, trade=best[2]),
                      f'Sell property to another player to avoid selling buildings. {describe_trade(best[2], _names(state))}')

    def _rescue_offer(self, state: GameState, ctx: Ctx, snap: Snap, me: int, owed: int, liq: int) -> TradeOffer | None:
        if trade_proposer(state) != me or not can_propose(state):
            return None
        P = self.params
        owner, bld = snap.owner, snap.bld
        fields = [sp for sp in range(40) if owner[sp] == me
                  and not (BOARD[sp].group is not None and group_built(bld, BOARD[sp].group))]
        packs = [(f,) for f in fields]
        if P.rescue_package >= 2:
            packs += [(a, b) for k, a in enumerate(fields) for b in fields[k + 1:]]
        cands = []
        v_me0, _ = evaluate(snap, ctx, me)
        for j in range(ctx.n):
            if j == me or not ctx.alive[j]:
                continue
            j_max = state.players[j].cash
            vj0, _ = evaluate(snap, ctx, j)
            for pk in packs:
                bank_val = sum(state.buildings[sp] * (BOARD[sp].house_cost // 2) + (0 if state.mortgaged[sp] else _M[sp]) for sp in pk)
                ask = owed - (liq - bank_val) + RESCUE_MARGIN
                ask = max(10, (ask + 9) // 10 * 10)
                if ask > j_max:
                    continue
                offer = _mk(me, j, pk, (), -ask)
                if offer in self._rescue_tried or validate_offer(state, offer) is not None:
                    continue
                after = apply_offer(snap, offer, ctx.alive)
                vj, _ = evaluate(after, ctx, j)
                vm, _ = evaluate(after, ctx, me)
                gain_j = vj - vj0
                cands.append((gain_j <= 0, v_me0 - vm, -gain_j, len(pk), j, pk, offer))
        if not cands:
            return None
        cands.sort(key=lambda t: t[:6])
        offer = cands[0][-1]
        self._rescue_tried.add(offer)
        return offer
