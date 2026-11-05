"""Frozen visible v7 input: 64 compact events and eight offers per direction."""
import numpy as np

from ..memory import LOG_KEEP, LOG_SPACES, PAIR_KEEP, round_of
from ..state import WINDOW_KINDS, GameState
from ..trade import trade_limit, trades_used, validate_offer
from .encoding_blocks import BUYABLE_LIST, BUYABLE_POS, SLOTS, Part, _Ctx
from .entries import (_COUNT_COL, _DECK_OFF, _MGMT, _ORIGIN_IDX, _OUTCOME_IDX, _SIGN, _STEP_KIND_IDX, _UP,
                      _UTILITY_POS, AGE_S, AMOUNT_S, C_CARDS0, CASH_S, COUNT_S, DECK_NAMES, PAIR_CONT, PHASES,
                      RENT_S, ROUND_S, STEP_CONT, STEP_INDEX, STEP_STRIDE, TURN_CONT, VOCAB, RULES_SIZE as _RULES_BASE,
                      EntryGroup, InputBlock, EntryFields, _cols, _fill_counts, _fill_rules,
                      _parts_counters, _space_idx, compact_log, field_card_table, scale,
                      MANAGEMENT, LOG_KINDS_V7, TURN_KINDS_V7, rent_table)
from .tradebuilder import DRAFT_ORIGINS, MAX_EDIT_STEPS, get_draft


def turn_acts7(state, player):
    return [a for a in state.memory.model_acts if a.who == player and a.turn == state.turn]


N_SPACES = len(BUYABLE_LIST)

P_IN, P_CASH, P_POS = 0, 1, 2
P_JAIL = P_POS + 40
P_JAIL_TURNS = P_JAIL + 1
P_JAIL_CARDS = P_JAIL + 2
P_CURRENT = P_JAIL_CARDS + len(DECK_NAMES)
P_PROPOSER, P_TARGET, P_HIGH, P_BIDDING, P_BID = range(P_CURRENT + 1, P_CURRENT + 6)
PLAYER = P_BID + 1
S_OWNER = 0
S_LEVEL, S_MORTGAGED, S_BUY, S_AUCTION, S_TAKEOVER, S_GIVE, S_GET, S_RENT = range(5, 13)
SPACE = S_RENT + 1
G_PHASE = 0
G_EXTRA = len(PHASES)
(G_HIGH_BID, G_ROUND, G_HOUSES, G_HOTELS, G_GIVE_CASH, G_GET_CASH, G_GIVE_CARDS, G_GET_CARDS,
 G_EXCHANGE) = range(G_EXTRA + 1, G_EXTRA + 10)
G_DICE = G_EXTRA + 10
G_DOUBLES = G_DICE + 2
GLOBAL = G_DOUBLES + 1
STATE_PLAYERS0 = 0
STATE_SPACES0 = SLOTS * PLAYER
STATE_GLOBAL0 = STATE_SPACES0 + N_SPACES * SPACE
STATE_SIZE = STATE_GLOBAL0 + GLOBAL

def _offer_sides(offer, player: int):
    o = offer
    if o.target == player:
        return o.get_spaces, o.give_spaces, o.get_cash, o.give_cash, o.get_cards, o.give_cards
    return o.give_spaces, o.get_spaces, o.give_cash, o.get_cash, o.give_cards, o.get_cards

def _fill_state(v: np.ndarray, o: int, ctx: _Ctx, params: dict) -> None:
    state, player, n, rel = ctx.state, ctx.player, ctx.n, ctx.rel
    for slot in range(n):
        p = state.players[(player + slot) % n]
        b = o + STATE_PLAYERS0 + slot * PLAYER
        v[b + P_IN] = float(not p.bankrupt)
        v[b + P_CASH] = scale(p.cash, CASH_S)
        if not p.bankrupt:
            v[b + P_POS + p.position] = 1.0
        v[b + P_JAIL] = float(p.in_jail)
        v[b + P_JAIL_TURNS] = p.jail_turns / 3.0
        for deck in p.jail_cards:
            v[b + P_JAIL_CARDS + DECK_NAMES.index(deck)] += 1.0
    pl = lambda pid, f: o + STATE_PLAYERS0 + rel(pid) * PLAYER + f
    v[pl(state.current, P_CURRENT)] = 1.0
    sp = o + STATE_SPACES0
    table = rent_table(state)
    for k, space in enumerate(BUYABLE_LIST):
        b = sp + k * SPACE
        owner = state.owner[space]
        v[b + S_OWNER + (0 if owner is None else 1 + rel(owner))] = 1.0
        v[b + S_LEVEL] = state.buildings[space] / 5.0
        v[b + S_MORTGAGED] = float(state.mortgaged[space])
        r = table[space]
        v[b + S_RENT] = r / 7.0 / 10.0 if k in _UTILITY_POS else scale(r, RENT_S)
    sf = lambda space, f: sp + BUYABLE_POS[space] * SPACE + f
    if state.pending_buy is not None:
        v[sf(state.pending_buy, S_BUY)] = 1.0
    for _, space in state.pending_takeovers:
        v[sf(space, S_TAKEOVER)] = 1.0
    g = o + STATE_GLOBAL0
    a = state.auction
    if a is not None:
        v[sf(a.space, S_AUCTION)] = 1.0
        v[g + G_HIGH_BID] = scale(a.high_bid, AMOUNT_S)
        if a.high_bidder is not None:
            v[pl(a.high_bidder, P_HIGH)] = 1.0
        for bidder in a.bidders:
            v[pl(bidder, P_BIDDING)] = 1.0
        m = state.memory
        for pid, bid in (getattr(m, "auction_bids", None) or {}).items():
            if bid:
                v[pl(pid, P_BID)] = scale(bid, AMOUNT_S)
    trade = state.trade
    if trade is not None:
        offer = trade.offer
        v[pl(offer.proposer, P_PROPOSER)] = 1.0
        v[pl(offer.target, P_TARGET)] = 1.0
        give_sp, get_sp, give_cash, get_cash, give_c, get_c = _offer_sides(offer, player)
        for s in give_sp:
            v[sf(s, S_GIVE)] = 1.0
        for s in get_sp:
            v[sf(s, S_GET)] = 1.0
        v[g + G_GIVE_CASH] = scale(give_cash, CASH_S)
        v[g + G_GET_CASH] = scale(get_cash, CASH_S)
        v[g + G_GIVE_CARDS] = float(give_c)
        v[g + G_GET_CARDS] = float(get_c)
        v[g + G_EXCHANGE] = trade.exchanges / max(1, state.config.max_trade_exchanges)
    v[g + G_PHASE + PHASES.index(state.phase)] = 1.0
    v[g + G_EXTRA] = float(state.extra_roll)
    v[g + G_ROUND] = scale(state.round, ROUND_S)
    v[g + G_HOUSES] = state.bank_houses / 32.0
    v[g + G_HOTELS] = state.bank_hotels / 12.0
    if state.last_roll:
        v[g + G_DICE] = state.last_roll[0] / 6.0
        v[g + G_DICE + 1] = state.last_roll[1] / 6.0
    v[g + G_DOUBLES] = state.doubles / 3.0

def _parts_state(params: dict) -> list[Part]:
    return [Part("player", STATE_PLAYERS0, PLAYER, PLAYER), Part("space", STATE_SPACES0, SPACE, SPACE)]

Z_PROPOSE, Z_COUNTER, Z_TRADES_LEFT, Z_SENT = range(_RULES_BASE, _RULES_BASE + 4)
RULES_SIZE = Z_SENT + 1

def _fill_rules7(v: np.ndarray, o: int, ctx: _Ctx, params: dict) -> None:

    _fill_rules(v, o, ctx, params)
    state, game = ctx.state, ctx.game
    if game is not None:
        if game.can_propose():
            v[o + Z_PROPOSE] = 1.0
            limit = max(1, trade_limit(state))
            v[o + Z_TRADES_LEFT] = max(limit - trades_used(state), 0) / limit
        v[o + Z_COUNTER] = float(game.can_counter())
    sent = sum(1 for a in turn_acts7(state, ctx.player) if a.kind in ("offer", "counter_offer"))
    v[o + Z_SENT] = min(sent / 3.0, 2.0)

PAY_DEBT = 0
PAY_WINDOW = 5
W_TRIGGER, W_QUEUE, W_REQUESTED = 10, 11, 12
PAY_PLAYER = 13
PAYMENTS_SPACES0 = SLOTS * PAY_PLAYER
PAYMENTS_GLOBAL0 = PAYMENTS_SPACES0 + N_SPACES
W_FLAG, W_KIND = 0, 1
PAYMENTS_SIZE = PAYMENTS_GLOBAL0 + 1 + len(WINDOW_KINDS)

def _fill_payments(v: np.ndarray, o: int, ctx: _Ctx, params: dict) -> None:

    state, rel = ctx.state, ctx.rel
    owed = np.zeros((SLOTS, 2, 1 + SLOTS))
    for d in state.debts:
        owed[rel(d.debtor), 0, 0 if d.creditor is None else 1 + rel(d.creditor)] += d.amount
    w = state.window
    if w is not None:
        for debtor, creditor, amount, _ in w.payments:
            owed[rel(debtor), 1, 0 if creditor is None else 1 + rel(creditor)] += amount
    for slot, side, col in zip(*np.nonzero(owed)):
        v[o + slot * PAY_PLAYER + (PAY_WINDOW if side else PAY_DEBT) + col] = scale(float(owed[slot, side, col]), AMOUNT_S)
    if w is None:
        return
    if w.trigger is not None:
        v[o + rel(w.trigger) * PAY_PLAYER + W_TRIGGER] = 1.0
    for k, pid in enumerate(w.queue):
        v[o + rel(pid) * PAY_PLAYER + W_QUEUE] = (k + 1) / 4.0
    for pid in w.requested:
        v[o + rel(pid) * PAY_PLAYER + W_REQUESTED] = 1.0
    if w.space is not None and w.space in BUYABLE_POS:
        v[o + PAYMENTS_SPACES0 + BUYABLE_POS[w.space]] = 1.0
    g = o + PAYMENTS_GLOBAL0
    v[g + W_FLAG] = float(state.phase.value == "trade_window")
    v[g + W_KIND + WINDOW_KINDS.index(w.kind)] = 1.0

def _parts_payments(params: dict) -> list[Part]:
    return [Part("player", 0, PAY_PLAYER, PAY_PLAYER), Part("space", PAYMENTS_SPACES0, 1, 1)]

D_TARGET = 0
DRAFT_SPACES0 = SLOTS
DRAFT_GLOBAL0 = DRAFT_SPACES0 + 2 * N_SPACES
D_PRESENT, D_COUNTER, D_GIVE_CASH, D_GET_CASH, D_GIVE_CARDS, D_GET_CARDS, D_VALID = range(7)
D_ORIGIN = 7
D_STEPS = D_ORIGIN + len(DRAFT_ORIGINS)
DRAFT_FIXED = DRAFT_GLOBAL0 + D_STEPS + 1
DRAFT_K = MAX_EDIT_STEPS

def _fill_draft(v: np.ndarray, o: int, ctx: _Ctx, params: dict) -> None:

    game, player = ctx.game, ctx.player
    draft = get_draft(game) if game is not None else None
    if draft is None or draft.proposer != player:
        return
    offer = draft.offer()
    v[o + D_TARGET + ctx.rel(draft.target)] = 1.0
    for s in draft.give:
        v[o + DRAFT_SPACES0 + BUYABLE_POS[s] * 2] = 1.0
    for s in draft.get:
        v[o + DRAFT_SPACES0 + BUYABLE_POS[s] * 2 + 1] = 1.0
    g = o + DRAFT_GLOBAL0
    v[g + D_PRESENT] = 1.0
    v[g + D_COUNTER] = float(draft.counter)
    v[g + D_GIVE_CASH] = scale(offer.give_cash, CASH_S)
    v[g + D_GET_CASH] = scale(offer.get_cash, CASH_S)
    v[g + D_GIVE_CARDS] = float(draft.give_cards)
    v[g + D_GET_CARDS] = float(draft.get_cards)
    v[g + D_VALID] = float(validate_offer(game.state, offer, raisable=True) is None)
    if draft.origin in _ORIGIN_IDX:
        v[g + D_ORIGIN + _ORIGIN_IDX[draft.origin] - 1] = 1.0
    v[g + D_STEPS] = draft.steps / MAX_EDIT_STEPS
    b = o + DRAFT_FIXED
    for st in reversed(draft.history[-DRAFT_K:]):
        v[b : b + STEP_STRIDE] = [_STEP_KIND_IDX[st.kind], _space_idx(st.space), float(st.sign),
                                  scale(st.amount, AMOUNT_S), scale(st.net, CASH_S)]
        b += STEP_STRIDE

def _parts_draft(params: dict) -> list[Part]:
    return [Part("player", D_TARGET, 1, 1), Part("space", DRAFT_SPACES0, 2, 2)]

def _fill_counters(v: np.ndarray, o: int, ctx: _Ctx, params: dict) -> None:
    m = ctx.state.memory
    if m is not None:
        _fill_counts(v, o, ctx, m)

_GIVE = [f"give{i}" for i in range(LOG_SPACES)]
_GET = [f"get{i}" for i in range(LOG_SPACES)]
_UPS = [f"up{i}" for i in range(LOG_SPACES)]
_DOWNS = [f"down{i}" for i in range(LOG_SPACES)]
_BIDS = [f"bids{i}" for i in range(SLOTS)]
_OPS = [f"ops{i}" for i in range(len(MANAGEMENT))]
PAIR_INDEX_V7 = [*_GIVE, *_GET, "outcome", "who", "direction"]
PAIR_CONT_V7 = list(PAIR_CONT)
PAIR_STRIDE_V7 = len(PAIR_INDEX_V7) + len(PAIR_CONT_V7)
EVENT_INDEX_V7 = ["kind", "who", "to", "space", "card", *_GIVE, *_GET, *_UPS, *_DOWNS]
EVENT_CONT_V7 = ["truncated", *_BIDS, *_OPS, "amount", "age"]
EVENT_STRIDE_V7 = len(EVENT_INDEX_V7) + len(EVENT_CONT_V7)
TURN_INDEX_V7 = ["turn_kind", "to", "space", *_GIVE, *_GET, "outcome", "origin", "phase", "window", "window_actor"]
TURN_CONT_V7 = list(TURN_CONT)
TURN_STRIDE_V7 = len(TURN_INDEX_V7) + len(TURN_CONT_V7)
TURN_N = 16
TURN_GLOBAL_V7 = len(TURN_KINDS_V7)
KIND_MANAGEMENT = len(LOG_KINDS_V7) + 1
VOCAB_V7 = {**VOCAB, "kind": KIND_MANAGEMENT + 1, "turn_kind": len(TURN_KINDS_V7) + 1, "direction": 3,
            "phase": len(PHASES) + 1, "window": len(WINDOW_KINDS) + 1}
_KIND7 = {k: i + 1 for i, k in enumerate(LOG_KINDS_V7)}
_TURN_KIND7 = {k: i + 1 for i, k in enumerate(TURN_KINDS_V7)}
_WINDOW_IDX = {k: i + 1 for i, k in enumerate(WINDOW_KINDS)}
_PHASE_IDX = {p.value: i + 1 for i, p in enumerate(PHASES)}
_E_AGE = len(EVENT_INDEX_V7) + EVENT_CONT_V7.index("age")
_AGES = np.array([scale(a, AGE_S) for a in range(512)])

def _fill_pair_offers(v: np.ndarray, o: int, ctx: _Ctx, params: dict) -> None:

    m = ctx.state.memory
    if m is None or not m.pair_offers:
        return
    K = params["pair_offers"]
    player, n = ctx.player, ctx.n
    now = round_of(ctx.state)
    c = {col: k for k, col in enumerate(PAIR_INDEX_V7 + PAIR_CONT_V7)}
    for slot in range(1, n):
        r = (player + slot) % n
        for d, (a, b_) in enumerate(((player, r), (r, player))):
            for k, po in enumerate(reversed(m.pair_offers[a][b_][-K:])):
                b = o + (((slot - 1) * 2 + d) * K + k) * PAIR_STRIDE_V7
                for j, s in enumerate(po.give[:LOG_SPACES]):
                    v[b + c["give0"] + j] = s + 1
                for j, s in enumerate(po.get[:LOG_SPACES]):
                    v[b + c["get0"] + j] = s + 1
                v[b + c["outcome"]] = _OUTCOME_IDX[po.outcome]
                v[b + c["who"]] = slot + 1
                v[b + c["direction"]] = d + 1
                v[b + c["truncated"]] = float(len(po.give) > LOG_SPACES or len(po.get) > LOG_SPACES)
                v[b + c["cash"]] = scale(po.cash, CASH_S)
                v[b + c["cards_give"]] = float(po.cards[0])
                v[b + c["cards_get"]] = float(po.cards[1])
                v[b + c["age"]] = scale(max(now - po.round, 0), AGE_S)
                v[b + c["counter"]] = float(po.counter)

_PAD = [0] * LOG_SPACES

def _rel1(pid, player: int, n: int) -> int:
    return 0 if pid is None else (pid - player) % n + 1

def _event_row(e, player: int, n: int) -> np.ndarray:
    give = [s + 1 for s in e.give[:LOG_SPACES]]
    get = [s + 1 for s in e.get[:LOG_SPACES]]
    bids = [0.0] * SLOTS
    for pid, bid in enumerate(e.bids):
        if bid:
            bids[(pid - player) % n] = scale(bid, AMOUNT_S)
    who, to = (e.to, e.who) if e.kind == "trade_reject" else (e.who, e.to)
    row = np.array([_KIND7[e.kind], _rel1(who, player, n), _rel1(to, player, n), _space_idx(e.space),
                    0 if e.card is None else 1 + _DECK_OFF[e.card[0]] + e.card[1],
                    *give, *_PAD[len(give):], *get, *_PAD[len(get):], *_PAD, *_PAD,
                    float(e.truncated), *bids, *[0.0] * len(_OPS), scale(e.amount, AMOUNT_S), 0.0], dtype=np.float32)
    return row

def _group_row(g: tuple, player: int, n: int) -> np.ndarray:

    first, last = g[0], g[-1]
    if first.kind not in _MGMT:
        return _event_row(first, player, n)
    up: list[int] = []
    down: list[int] = []
    counts = [0] * len(MANAGEMENT)
    balance = 0
    for x in g:
        counts[_COUNT_COL[x.kind]] += 1
        balance += _SIGN[x.kind] * x.amount
        if x.space is not None:
            side = up if x.kind in _UP else down
            if x.space not in side:
                side.append(x.space)
    ups = [s + 1 for s in up[:LOG_SPACES]]
    downs = [s + 1 for s in down[:LOG_SPACES]]
    row = np.array([KIND_MANAGEMENT, _rel1(first.who, player, n), 0, 0, 0, *_PAD, *_PAD,
                    *ups, *_PAD[len(ups):], *downs, *_PAD[len(downs):],
                    float(len(up) > LOG_SPACES or len(down) > LOG_SPACES), *[0.0] * SLOTS,
                    *[scale(c, COUNT_S) for c in counts], scale(balance, AMOUNT_S), 0.0], dtype=np.float32)
    return row

def _log_block(log: list, limit: int, player: int, n: int) -> tuple[np.ndarray, np.ndarray]:

    groups = compact_log(log, limit)
    out = (np.concatenate([_group_row(g, player, n) for g in groups]), np.array([g[-1].round for g in groups]))
    return out

def _fill_events(v: np.ndarray, o: int, ctx: _Ctx, params: dict) -> None:

    m = ctx.state.memory
    log = getattr(m, "log", None) if m is not None else None
    if not log:
        return
    rows, rounds = _log_block(log, params["events"], ctx.player, ctx.n)
    flat = v[o : o + rows.size]
    flat[:] = rows
    ages = np.maximum(round_of(ctx.state) - rounds, 0)
    col = flat[_E_AGE::EVENT_STRIDE_V7]
    if ages.max() < len(_AGES):
        col[:] = _AGES[ages]
    else:
        col[:] = [_AGES[a] if a < len(_AGES) else scale(int(a), AGE_S) for a in ages]

def _fill_own_turn(v: np.ndarray, o: int, ctx: _Ctx, params: dict) -> None:

    acts = turn_acts7(ctx.state, ctx.player)
    if not acts:
        return
    counts = [0] * TURN_GLOBAL_V7
    for a in acts:
        counts[_TURN_KIND7[a.kind] - 1] += 1
    v[o : o + TURN_GLOBAL_V7] = [scale(c, COUNT_S) for c in counts]
    player, n = ctx.player, ctx.n
    b = o + TURN_GLOBAL_V7
    for a in reversed(acts[-TURN_N:]):
        give = [s + 1 for s in a.give[:LOG_SPACES]]
        get = [s + 1 for s in a.get[:LOG_SPACES]]
        v[b : b + TURN_STRIDE_V7] = [
            _TURN_KIND7[a.kind], _rel1(a.to, player, n), _space_idx(a.space),
            *give, *_PAD[len(give):], *get, *_PAD[len(get):],
            _OUTCOME_IDX.get(a.outcome, 0), _ORIGIN_IDX.get(a.origin, 0), _PHASE_IDX.get(a.phase, 0),
            _WINDOW_IDX.get(a.window, 0), _rel1(a.trigger, player, n),
            float(len(a.give) > LOG_SPACES or len(a.get) > LOG_SPACES), scale(a.amount, AMOUNT_S),
            float(a.cards[0]), float(a.cards[1]), a.steps / MAX_EDIT_STEPS]
        b += TURN_STRIDE_V7

V7_DEFAULT_PARAMS = {"events": 64, "pair_offers": 8}
V7_MAXIMUM = {"events": LOG_KEEP, "pair_offers": PAIR_KEEP}

V7_BLOCKS: dict[str, InputBlock] = {
    "state": InputBlock("Fixed input block", lambda p: STATE_SIZE, _fill_state, _parts_state),
    "field_card": InputBlock("Fixed input block", lambda p: 0, None),
    "rules": InputBlock("Fixed input block", lambda p: RULES_SIZE, _fill_rules7),
    "payments": InputBlock("Fixed input block",
                         lambda p: PAYMENTS_SIZE, _fill_payments, _parts_payments),
    "draft": InputBlock("Fixed input block", lambda p: DRAFT_FIXED + DRAFT_K * STEP_STRIDE, _fill_draft,
                     _parts_draft),
    "counters": InputBlock("Fixed input block", lambda p: C_CARDS0, _fill_counters, _parts_counters),
    "pair_offers": InputBlock("Fixed input block",
                          lambda p: 2 * (SLOTS - 1) * p["pair_offers"] * PAIR_STRIDE_V7, _fill_pair_offers,
                          default=8, maximum=PAIR_KEEP),
    "events": InputBlock("Fixed input block", lambda p: p["events"] * EVENT_STRIDE_V7, _fill_events, default=64,
                        maximum=LOG_KEEP),
    "own_turn": InputBlock("Fixed input block", lambda p: TURN_GLOBAL_V7 + TURN_N * TURN_STRIDE_V7, _fill_own_turn),
}

ENTRY_BLOCKS_V7 = {"draft": ("step", DRAFT_FIXED, DRAFT_K, STEP_STRIDE, STEP_INDEX, STEP_CONT, "edit_kind"),
                   "own_turn": ("turn", TURN_GLOBAL_V7, TURN_N, TURN_STRIDE_V7, TURN_INDEX_V7, TURN_CONT_V7, "turn_kind")}
class V7Encoder:

    entry_blocks = ("pair_offers", "events")

    def __init__(self):
        self.params = {"events": 64, "pair_offers": 8}
        self.name = "rentcraft-visible-v7-offers8"
        self.blocks = list(V7_BLOCKS)
        self.layout: dict[str, tuple[int, int]] = {}
        o = 0
        for b in self.blocks:
            size = V7_BLOCKS[b].size(self.params)
            self.layout[b] = (o, size)
            o += size
        self.size = o
        K, N = self.params["pair_offers"], self.params["events"]
        f = EntryFields(event0=self.layout["events"][0], event_n=N, event_stride=EVENT_STRIDE_V7,
                     event_index_cols=_cols(EVENT_INDEX_V7, EVENT_CONT_V7)[0],
                     event_cont_cols=_cols(EVENT_INDEX_V7, EVENT_CONT_V7)[1],
                     pair0=self.layout["pair_offers"][0], pair_n=2 * (SLOTS - 1) * K, pair_stride=PAIR_STRIDE_V7,
                     pair_index_cols=_cols(PAIR_INDEX_V7, PAIR_CONT_V7)[0],
                     pair_cont_cols=_cols(PAIR_INDEX_V7, PAIR_CONT_V7)[1], pair_k=K, vocab=dict(VOCAB_V7))
        for b, (kind, glob, n, stride, index, cont, key) in ENTRY_BLOCKS_V7.items():
            f.extra.append(EntryGroup(kind, self.layout[b][0] + glob, n, stride, *_cols(index, cont), key))
        self.fields = f
        self.field_card = True
        self._fills = [(V7_BLOCKS[b].fill, self.layout[b][0]) for b in self.blocks if V7_BLOCKS[b].fill is not None]

    def parts(self, block: str) -> list[Part]:
        return V7_BLOCKS[block].parts(self.params)

    def encode(self, state: GameState, player: int, game=None) -> np.ndarray:
        v = np.zeros(self.size, dtype=np.float32)
        ctx = _Ctx(state, player, game)
        for fill, o in self._fills:
            fill(v, o, ctx, self.params)
        return v

