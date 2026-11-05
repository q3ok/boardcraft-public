"""Frozen legacy block input, using public board knowledge."""
from dataclasses import dataclass
from functools import cached_property
from operator import itemgetter

import numpy as np

from ..bots.odds import landing, roll_probs
from ..bots.trading import trade_score
from ..board import BOARD, GROUPS
from ..actions import Phase
from ..board import BUYABLE, HOTEL, Kind
from ..buildings import unmortgage_cost
from ..cards import DECKS
from ..memory import NOTABLE, RECENT
from ..state import GameState
from ..trade import trades_used, validate_offer
from .actions import StandardActionSpaceV1
from ..rent import rent_for
from .tradebuilder import MAX_EDIT_STEPS, get_draft


def rent_table(state):
    """Scheduled rents at a seven-pip roll, exactly as the frozen inputs require."""
    return tuple(float(rent_for(state, i, 7)) if state.owner[i] is not None else 0.0 for i in range(40))


PHASES = [Phase.PRE_ROLL, Phase.BUY_DECISION, Phase.AUCTION, Phase.RAISE_FUNDS, Phase.TRADE_RESPONSE, Phase.POST_ROLL,
          Phase.GAME_OVER]

PHASE_SLOT = {p: i for i, p in enumerate(PHASES)} | {Phase.TRADE_WINDOW: PHASES.index(Phase.POST_ROLL),
                                                     Phase.MORTGAGE_TAKEOVER: PHASES.index(Phase.POST_ROLL)}
BUYABLE_LIST = sorted(BUYABLE)
BUYABLE_POS = {s: k for k, s in enumerate(BUYABLE_LIST)}
SLOTS = 4

_ROLLS = list(roll_probs().items())

H_ASSETS, H_OWNED, H_BUILDINGS, H_MORTGAGED, H_RAISE, H_UNMORTGAGE, H_HOUSES, H_HOTELS = range(8)

def _space_sums(space: int, level: int, mortgaged: bool) -> list[int]:
    s = BOARD[space]
    return [(s.price // 2 if mortgaged else s.price) + level * s.house_cost,
            1, level, int(mortgaged),
            level * s.house_cost // 2 + (0 if mortgaged else s.mortgage_value),
            unmortgage_cost(space) if mortgaged else 0,
            level if level < HOTEL else 0, int(level == HOTEL)]

def _space_row(who: int, level: int, mortgaged: bool) -> list[float]:
    return [float(who == k) for k in range(SLOTS + 1)] + [level / 5.0, float(mortgaged)]

_N_BUY = len(BUYABLE_LIST)
_PICK_BUY = itemgetter(*BUYABLE_LIST)
_BUY_IDX = np.arange(_N_BUY)
_SPACE_SUMS = np.array([[[_space_sums(sp, b, m) for m in (False, True)] for b in range(HOTEL + 1)] for sp in BUYABLE_LIST])
_SPACE_ROWS = np.array([[[_space_row(w, b, m) for m in (False, True)] for b in range(HOTEL + 1)] for w in range(SLOTS + 1)],
                       dtype=np.float32)
_OWNER_ONE_HOT = np.eye(SLOTS + 1, dtype=np.int64)

def _clip(x: float, limit: float) -> float:
    return min(max(x, -limit), limit)

class _Ctx:
    def __init__(self, state: GameState, player: int, game=None):
        self.state, self.player, self.game = state, player, game
        self.n = len(state.players)
    def rel(self, pid: int) -> int:

        return (pid - self.player) % self.n

    @cached_property
    def owner(self) -> np.ndarray:

        return np.array([-1 if o is None else o for o in _PICK_BUY(self.state.owner)])

    @cached_property
    def buildings(self) -> np.ndarray:
        return np.array(_PICK_BUY(self.state.buildings))

    @cached_property
    def mortgaged(self) -> np.ndarray:

        return np.array(_PICK_BUY(self.state.mortgaged), dtype=np.int8)

    @cached_property
    def holdings(self) -> list[list[int]]:

        per_space = _SPACE_SUMS[_BUY_IDX, self.buildings, self.mortgaged]
        who = _OWNER_ONE_HOT[self.owner]
        return (who.T @ per_space)[: self.n].tolist()

    @cached_property
    def worth(self) -> list[int]:

        return [p.cash + h[H_ASSETS] for p, h in zip(self.state.players, self.holdings)]

class _BaseEncoder:

    ACTIVE, BANKRUPT, CASH, WORTH, POS = 0, 1, 2, 3, 4
    JAIL = POS + 40
    JAIL_TURNS = JAIL + 1
    JAIL_CARDS = JAIL + 2
    SLOT = JAIL + 3
    SPACE = 7
    def __init__(self):
        self._phase0 = 0
        self._slots0 = len(PHASES) + 1
        self._spaces0 = self._slots0 + SLOTS * self.SLOT
        self._ctx0 = self._spaces0 + len(BUYABLE_LIST) * self.SPACE

        self.trade_offset = self._ctx0 + 28 + 28 + 2 + 1 + 1 + 2
        self.size = self.trade_offset + 56 + 2 + 2 + 2
    def buy_pos(self, space: int) -> int:
        return BUYABLE_POS[space]
    def slot_offset(self, slot: int) -> int:
        return self._slots0 + slot * self.SLOT
    def owner_offset(self, space: int) -> int:
        return self._spaces0 + BUYABLE_POS[space] * self.SPACE
    def encode(self, state: GameState, player: int, game=None) -> np.ndarray:
        v = np.zeros(self.size, dtype=np.float32)
        self._fill(v, _Ctx(state, player, game))
        return v
    def _fill(self, v: np.ndarray, ctx: _Ctx) -> None:

        state, player, n = ctx.state, ctx.player, ctx.n
        v[self._phase0 + PHASE_SLOT[state.phase]] = 1.0
        v[len(PHASES)] = float(state.extra_roll)

        for slot in range(n):
            pid = (player + slot) % n
            p = state.players[pid]
            o = self.slot_offset(slot)
            v[o : o + 4] = (1.0, float(p.bankrupt), min(p.cash / 1500.0, 5.0), min(ctx.worth[pid] / 5000.0, 5.0))
            if not p.bankrupt:
                v[o + self.POS + p.position] = 1.0
            v[o + self.JAIL : o + self.SLOT] = (float(p.in_jail), p.jail_turns / 3.0, float(len(p.jail_cards)))

        owner = ctx.owner
        who = np.where(owner < 0, 0, 1 + (owner - player) % n)
        v[self._spaces0 : self._spaces0 + _N_BUY * self.SPACE] = _SPACE_ROWS[who, ctx.buildings, ctx.mortgaged].ravel()
        c = self._ctx0
        if state.pending_buy is not None:
            v[c + BUYABLE_POS[state.pending_buy]] = 1.0
        if state.phase is Phase.MORTGAGE_TAKEOVER and state.pending_takeovers:
            v[c + BUYABLE_POS[state.pending_takeovers[0][1]]] = 1.0
        c += 28
        if state.auction is not None:
            a = state.auction
            v[c + BUYABLE_POS[a.space]] = 1.0
            v[c + 28] = min(a.high_bid / max(BOARD[a.space].price, 1), 5.0)
            v[c + 29] = float(a.high_bidder == player)
        c += 30
        v[c] = min(sum(d.amount for d in state.debts if d.debtor == player) / 500.0, 5.0)
        v[c + 1] = min(state.round / 200.0, 2.0)
        v[c + 2] = state.bank_houses / 32.0
        v[c + 3] = state.bank_hotels / 12.0
        t = self.trade_offset
        trade = state.trade
        if trade is not None:
            o = trade.offer
            mine_is_proposer = o.proposer == player
            give_sp, get_sp = (o.give_spaces, o.get_spaces) if mine_is_proposer else (o.get_spaces, o.give_spaces)
            give_cash, get_cash = (o.give_cash, o.get_cash) if mine_is_proposer else (o.get_cash, o.give_cash)
            give_c, get_c = (o.give_cards, o.get_cards) if mine_is_proposer else (o.get_cards, o.give_cards)
            for sp in give_sp:
                v[t + BUYABLE_POS[sp] * 2] = 1.0
            for sp in get_sp:
                v[t + BUYABLE_POS[sp] * 2 + 1] = 1.0
            v[t + 56] = min(give_cash / 1500.0, 5.0)
            v[t + 57] = min(get_cash / 1500.0, 5.0)
            v[t + 58] = float(give_c)
            v[t + 59] = float(get_c)
            v[t + 60] = trade.exchanges / max(1, state.config.max_trade_exchanges)
        v[t + 61] = trades_used(state) / max(1, state.config.max_trades_per_turn)

class _CandidateFeatures:

    C_PRESENT, C_TARGET, C_GIVE_SPACES, C_GET_SPACES, C_GIVE_CASH, C_GET_CASH, C_MY_SET, C_THEIR_SET, C_SCORE = range(9)
    CAND = 9
    N_CANDS = 5

def _fill_candidates(v: np.ndarray, o0: int, ctx: _Ctx) -> None:
    state, player, game = ctx.state, ctx.player, ctx.game
    if game is None:
        return
    E = _CandidateFeatures
    space = StandardActionSpaceV1()
    cands = list(space.proposal_candidates(game, player))
    cands += [None] * (E.N_CANDS - 1 - len(cands))
    cands.append(space.counter_candidate(game, player))
    for k, offer in enumerate(cands):
        if offer is None:
            continue
        o = o0 + k * E.CAND
        me_first = offer.proposer == player
        other = offer.target if me_first else offer.proposer
        give_sp, get_sp = (offer.give_spaces, offer.get_spaces) if me_first else (offer.get_spaces, offer.give_spaces)
        give_cash, get_cash = (offer.give_cash, offer.get_cash) if me_first else (offer.get_cash, offer.give_cash)

        v[o : o + E.CAND] = (1.0, ctx.rel(other) / 3.0, len(give_sp) / 3.0, len(get_sp) / 3.0,
                             min(give_cash / 1500.0, 5.0), min(get_cash / 1500.0, 5.0),
                             float(_completes(state, player, get_sp, give_sp)), float(_completes(state, other, give_sp, get_sp)),
                             _clip(trade_score(state, offer, player) / 1000.0, 5.0))

class _ContextFeatures:

    CURRENT = 0
    TRADE_WITH = 4
    TRADE_MINE = 8
    AUCTION_HIGH = 9
    AUCTION_IN = 13
    LIMIT = 17
    ROUNDS_LEFT = 18
    DICE = 19
    DOUBLES = 21
    EXTRA = 22

def _fill_context(v: np.ndarray, o: int, ctx: _Ctx) -> None:
    E = _ContextFeatures
    state, player, rel = ctx.state, ctx.player, ctx.rel
    v[o + E.CURRENT + rel(state.current)] = 1.0
    if state.trade is not None:
        offer = state.trade.offer
        other = offer.target if offer.proposer == player else offer.proposer
        v[o + E.TRADE_WITH + rel(other)] = 1.0
        v[o + E.TRADE_MINE] = float(offer.proposer == player)
    a = state.auction
    if a is not None:
        if a.high_bidder is not None:
            v[o + E.AUCTION_HIGH + rel(a.high_bidder)] = 1.0
        for b in a.bidders:
            v[o + E.AUCTION_IN + rel(b)] = 1.0
    limit = state.config.max_rounds
    if limit:
        v[o + E.LIMIT] = 1.0
        v[o + E.ROUNDS_LEFT] = min(max(limit - state.round, 0) / 200.0, 2.0)
    if state.last_roll:
        v[o + E.DICE] = state.last_roll[0] / 6.0
        v[o + E.DICE + 1] = state.last_roll[1] / 6.0
    v[o + E.DOUBLES] = state.doubles / 3.0

class _MemoryFeatures:

    M_PROPOSALS, M_ACCEPTED, M_REJECTED, M_AUCTIONS, M_OVERPAY, M_PAID_ME, M_I_PAID = range(7)
    MEM = 7

_KIND_POS = {kind: k for k, kind in enumerate(NOTABLE)}

def _fill_memory(v: np.ndarray, o0: int, ctx: _Ctx) -> None:
    E = _MemoryFeatures
    m = ctx.state.memory
    if m is None:
        return
    player, n = ctx.player, ctx.n
    event = len(NOTABLE) + SLOTS + 1
    recent0 = o0 + SLOTS * E.MEM
    for slot in range(n):
        pid = (player + slot) % n
        o = o0 + slot * E.MEM
        overpay = min(m.auction_paid[pid] / m.auction_list[pid], 3.0) if m.auction_list[pid] else 0.0

        v[o : o + E.MEM] = (min(m.proposals[pid] / 5.0, 3.0), min(m.accepted[pid] / 5.0, 3.0), min(m.rejected[pid] / 5.0, 3.0),
                            min(m.auctions_won[pid] / 5.0, 3.0), overpay, min(m.paid[pid][player] / 2000.0, 5.0),
                            min(m.paid[player][pid] / 2000.0, 5.0))
    for k, (kind, who) in enumerate(reversed(m.recent[-RECENT:])):
        o = recent0 + k * event
        v[o + _KIND_POS[kind]] = 1.0
        v[o + len(NOTABLE) + (SLOTS if who is None else ctx.rel(who))] = 1.0

class _RentBlock:
    R_RENT, R_PRICE, R_HOUSE, R_EXPECTED, R_NEXT = range(5)
    PER_SPACE = 5
    RENT_SIZE = len(BUYABLE_LIST) * PER_SPACE + 2

def _rent_spaces(state: GameState, table) -> np.ndarray:
    E = _RentBlock
    p = landing()
    out = np.zeros(len(BUYABLE_LIST) * E.PER_SPACE, dtype=np.float32)
    for space in BUYABLE_LIST:
        s = BOARD[space]
        r = table[space]
        o = BUYABLE_POS[space] * E.PER_SPACE
        out[o + E.R_RENT] = min(r / 1000.0, 3.0)
        out[o + E.R_PRICE] = s.price / 400.0
        out[o + E.R_HOUSE] = s.house_cost / 200.0
        out[o + E.R_EXPECTED] = min(p[space] * r / 50.0, 3.0)
        owner = state.owner[space]
        level = state.buildings[space]
        if s.kind is Kind.PROPERTY and owner is not None and level < 5 and all(state.owner[i] == owner for i in GROUPS[s.group]):
            out[o + E.R_NEXT] = min(s.rents[level + 1] / 1000.0, 3.0)
    return out

def _fill_rent(v: np.ndarray, o0: int, ctx: _Ctx) -> None:
    E = _RentBlock
    state, player = ctx.state, ctx.player
    table = rent_table(state)
    n = len(BUYABLE_LIST) * E.PER_SPACE
    v[o0 : o0 + n] = _rent_spaces(state, table)
    exp = worst = 0.0
    pos = state.players[player].position
    for d, pd in _ROLLS:
        t = (pos + d) % 40
        owner = state.owner[t]
        if owner is None or owner == player or t not in BUYABLE:
            continue
        exp += pd * table[t]
        worst = max(worst, table[t])
    risk0 = o0 + len(BUYABLE_LIST) * E.PER_SPACE
    v[risk0] = min(exp / 500.0, 3.0)
    v[risk0 + 1] = min(worst / 2000.0, 3.0)

_COLOR_OF = {s: color for color, spaces in GROUPS.items() for s in spaces}

def _completes(state: GameState, pid: int, gained, lost) -> bool:
    gained, lost = set(gained), set(lost)
    for color in {_COLOR_OF[s] for s in gained if s in _COLOR_OF}:
        spaces = GROUPS[color]
        before = all(state.owner[s] == pid for s in spaces)
        after = all((state.owner[s] == pid and s not in lost) or s in gained for s in spaces)
        if after and not before:
            return True
    return False

COLORS = list(GROUPS)

def _fill_sets(v: np.ndarray, o: int, ctx: _Ctx) -> None:
    for k, color in enumerate(COLORS):
        owners = {ctx.state.owner[i] for i in GROUPS[color]}
        if len(owners) == 1 and None not in owners:
            v[o + ctx.rel(owners.pop()) * len(COLORS) + k] = 1.0

def _fill_debts(v: np.ndarray, o: int, ctx: _Ctx) -> None:
    for d in ctx.state.debts:
        k = o + ctx.rel(d.debtor)
        v[k] = min(v[k] + d.amount / 500.0, 5.0)

DRAFT_SIZE = 1 + 1 + SLOTS + 2 * 28 + 2 + 2 + 1 + 1 + 2 + 1

def _fill_draft(v: np.ndarray, o: int, ctx: _Ctx) -> None:
    state, player, game = ctx.state, ctx.player, ctx.game
    draft = get_draft(game) if game is not None else None
    if draft is None or draft.proposer != player:
        return
    offer = draft.offer()
    v[o] = 1.0
    v[o + 1] = float(draft.counter)
    v[o + 2 + ctx.rel(draft.target)] = 1.0
    p = o + 2 + SLOTS
    for sp in draft.give:
        v[p + BUYABLE_POS[sp] * 2] = 1.0
    for sp in draft.get:
        v[p + BUYABLE_POS[sp] * 2 + 1] = 1.0
    p += 2 * 28
    v[p] = min(offer.give_cash / 1500.0, 5.0)
    v[p + 1] = min(offer.get_cash / 1500.0, 5.0)
    v[p + 2] = float(draft.give_cards)
    v[p + 3] = float(draft.get_cards)
    v[p + 4] = draft.steps / MAX_EDIT_STEPS
    valid = validate_offer(game.state, offer, raisable=True) is None
    if valid:
        v[p + 5] = _clip(trade_score(state, offer, player) / 1000.0, 5.0)
    v[p + 6] = float(_completes(state, player, draft.get, draft.give))
    v[p + 7] = float(_completes(state, draft.target, draft.give, draft.get))
    v[p + 8] = float(valid)

LIQUIDITY_SIZE = SLOTS * 2 + 1

CARD_KINDS = ("jail", "start", "move", "collect", "pay", "repairs", "jail_free")
CARD_DECK_SIZE = 3 + len(CARD_KINDS)
CARDS_SIZE = 2 * CARD_DECK_SIZE

CHANGE_FIELDS = ("cash", "worth", "owned", "buildings", "mortgaged", "paid_me", "i_paid")
CHANGES_SIZE = SLOTS * len(CHANGE_FIELDS)

def _snapshot(ctx: _Ctx) -> dict:
    h = ctx.holdings
    return {"cash": [p.cash for p in ctx.state.players], "worth": ctx.worth, "owned": [x[H_OWNED] for x in h],
            "buildings": [x[H_BUILDINGS] for x in h], "mortgaged": [x[H_MORTGAGED] for x in h]}

def _fill_changes(v: np.ndarray, o: int, ctx: _Ctx) -> None:
    m, player, n = ctx.state.memory, ctx.player, ctx.n
    if m is None or player not in m.model_snaps:
        return
    old, now = m.model_snaps[player], _snapshot(ctx)
    scale = (("cash", 500.0), ("worth", 1000.0), ("owned", 1.0), ("buildings", 5.0), ("mortgaged", 1.0))
    for slot in range(n):
        pid = (player + slot) % n
        b = o + slot * len(CHANGE_FIELDS)
        v[b : b + len(scale)] = [_clip((now[key][pid] - old[key][pid]) / div, 5.0) for key, div in scale]
        if pid != player:
            v[b + 5] = min((m.paid[pid][player] - old["paid"][pid][player]) / 500.0, 5.0)
            v[b + 6] = min((m.paid[player][pid] - old["paid"][player][pid]) / 500.0, 5.0)

def card_kind(card) -> int:
    e = card.effect
    if e == "go_to_jail":
        return 0
    if e == "advance_to" and card.target == 0:
        return 1
    if e in ("advance_to", "advance_nearest_railroad", "advance_nearest_utility", "back_three"):
        return 2
    if e in ("collect", "collect_each"):
        return 3
    if e in ("pay", "pay_each"):
        return 4
    if e == "repairs":
        return 5
    return 6

def _cash_coefs(card) -> tuple[int, int, int, int]:
    e = card.effect
    if e == "collect":
        return card.value, 0, 0, 0
    if e == "collect_each":
        return 0, card.value, 0, 0
    if e == "pay":
        return -card.value, 0, 0, 0
    if e == "pay_each":
        return 0, -card.value, 0, 0
    if e == "repairs":
        return 0, 0, -card.value, -card.value2
    if e == "advance_to" and card.target == 0:
        return 200, 0, 0, 0
    return 0, 0, 0, 0

def _cash_inputs(ctx: _Ctx) -> np.ndarray:
    rivals = sum(1 for i, p in enumerate(ctx.state.players) if i != ctx.player and not p.bankrupt)
    mine = ctx.holdings[ctx.player]
    return np.array([1, rivals, mine[H_HOUSES], mine[H_HOTELS]])

def card_cash(card, state: GameState, player: int) -> int:
    return int(np.dot(_cash_coefs(card), _cash_inputs(_Ctx(state, player))))

_DECK_KIND = {deck: [card_kind(c) for c in cards] for deck, cards in DECKS.items()}
_DECK_CASH = {deck: np.array([_cash_coefs(c) for c in cards]) for deck, cards in DECKS.items()}

def _fractions(n: int) -> np.ndarray:
    out = np.zeros(n + 1, dtype=np.float32)
    for c in range(1, n + 1):
        out[c] = out[c - 1]
        out[c] += 1.0 / n
    return out

_FRACTIONS = [_fractions(n) for n in range(max(len(cards) for cards in DECKS.values()) + 1)]

def _fill_cards(v: np.ndarray, o: int, ctx: _Ctx) -> None:
    state = ctx.state
    seen = state.memory.seen_cards if state.memory is not None else {}
    inputs = _cash_inputs(ctx)
    for k, deck in enumerate(("opportunity", "community_fund")):
        p = o + k * CARD_DECK_SIZE
        held = {c.id for c in DECKS[deck] if c.effect == "jail_free" and any(deck in pl.jail_cards for pl in state.players)}
        known = seen.get(deck, set())
        unseen = [c.id for c in DECKS[deck] if c.id not in known and c.id not in held]
        bottom = state.memory.known_bottom[deck]
        cards = unseen or bottom[:1]
        if not cards:
            continue
        v[p] = float(not unseen)
        v[p + 1] = len(unseen) / 16.0
        kinds, cash = _DECK_KIND[deck], (_DECK_CASH[deck] @ inputs).tolist()
        counts, total = [0] * len(CARD_KINDS), 0
        for c in cards:
            counts[kinds[c]] += 1
            total += cash[c]
        v[p + 2 : p + 2 + len(CARD_KINDS)] = _FRACTIONS[len(cards)][counts]
        v[p + 2 + len(CARD_KINDS)] = _clip(total / len(cards) / 200.0, 5.0)

def _fill_liquidity(v: np.ndarray, o: int, ctx: _Ctx) -> None:
    h = ctx.holdings
    for pid, p in enumerate(ctx.state.players):
        if p.bankrupt:
            continue
        slot = ctx.rel(pid)
        raise_cash = h[pid][H_RAISE]
        v[o + slot * 2 : o + slot * 2 + 2] = (min(raise_cash / 1500.0, 5.0), min((p.cash + raise_cash) / 1500.0, 5.0))
    v[o + SLOTS * 2] = min(h[ctx.player][H_UNMORTGAGE] / 1500.0, 5.0)

HISTORY_PER_RIVAL = 8
HISTORY_SIZE = (SLOTS - 1) * HISTORY_PER_RIVAL

def _fill_history(v: np.ndarray, o: int, ctx: _Ctx) -> None:
    m, player, n = ctx.state.memory, ctx.player, ctx.n
    if m is None or not m.offers:
        return
    turn = ctx.state.turn
    for slot in range(1, n):
        rival = (player + slot) % n
        base = o + (slot - 1) * HISTORY_PER_RIVAL
        for k, (a, b) in enumerate(((player, rival), (rival, player))):
            p = base + 4 * k
            last = m.last_turn[a][b]
            v[p : p + 4] = (min(m.streak[a][b] / 3.0, 3.0), min(m.rejects[a][b] / 5.0, 3.0), _clip(m.last_cash[a][b] / 500.0, 3.0),
                            1.0 if last < 0 else min((turn - last) / 20.0, 1.0))

MINI_BLOCKS = {
    "state": (516, None),
    "trade_candidates": (45, _fill_candidates),
    "context": (22, _fill_context),
    "memory": (142, _fill_memory),
    "rents": (142, _fill_rent),
    "complete_sets": (32, _fill_sets),
    "debts": (4, _fill_debts),
    "trade_history": (24, _fill_history),
    "liquidity": (9, _fill_liquidity),
    "draft": (71, _fill_draft),
    "cards": (20, _fill_cards),
    "changes": (28, _fill_changes),
}


@dataclass(frozen=True)
class Part:
    kind: str
    start: int
    feats: int
    item_stride: int
    feat_stride: int = 1
    first: int = 0


class MiniEncoder:
    name = "rentcraft-legacy-blocks-mini-evo5"
    def __init__(self):
        self.layout = {}
        offset = 0
        for block, (size, _) in MINI_BLOCKS.items():
            self.layout[block] = (offset, size)
            offset += size
        self.size = offset
        self._base = _BaseEncoder()
    def encode(self, state, player, game=None):
        v = np.zeros(self.size, dtype=np.float32)
        ctx = _Ctx(state, player, game)
        self._base._fill(v, ctx)
        for block, (_, fill) in MINI_BLOCKS.items():
            if fill is not None:
                fill(v, self.layout[block][0], ctx)
        return v
