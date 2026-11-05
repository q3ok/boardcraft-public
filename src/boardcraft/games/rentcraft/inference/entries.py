"""Entry layouts and the rule-derived helpers required by the fixed v7 profile."""
import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from ..actions import Phase
from ..board import BOARD, GROUPS, Kind, RAILROAD_RENTS, UTILITY_MULTIPLIERS
from ..buildings import unmortgage_cost
from ..memory import DECK_SIZE, LOG_KINDS, LOG_SPACES, LOG_KEEP, PAIR_KEEP
from .encoding_blocks import BUYABLE_LIST, BUYABLE_POS, SLOTS, Part, _Ctx, rent_table
from .tradebuilder import DRAFT_ORIGINS, STEP_KINDS

MANAGEMENT = ("build", "sell_building", "mortgage", "unmortgage")
TURN_KINDS = ("buy", "decline_buy", "build", "sell_building", "mortgage", "unmortgage", "pay_jail_fine",
              "use_jail_card", "offer", "counter_offer", "accept_trade", "reject_trade", "pass_window",
              "pay_interest", "abandoned_draft")
TURN_KINDS_V7 = TURN_KINDS + ("auction_won", "bid")
LOG_KINDS_V7 = LOG_KINDS + ("jail_card_return",)
PHASES = list(Phase)
TRADE_WINDOW_LEVELS = ("off", "payments", "full")
N_SPACES = len(BUYABLE_LIST)
CASH_S, AMOUNT_S, RENT_S, CARD_S, COUNT_S, ROUND_S, AGE_S = 500.0, 100.0, 50.0, 100.0, 5.0, 20.0, 5.0


def scale(x, s):
    return math.copysign(math.log1p(abs(x) / s), x) if x else 0.0


COLORS = list(GROUPS)
FIELD_CARD_COLS = (["price", "house_cost", "mortgage", "unmortgage",
                    "rent", "set_rent", "house1", "house2", "house3", "house4", "hotel",
                    "stations1", "stations2", "stations3", "stations4", "utility_x4", "utility_x10"]
                   + COLORS + ["station", "utility"])

def field_card_table() -> np.ndarray:

    c = {name: k for k, name in enumerate(FIELD_CARD_COLS)}
    t = np.zeros((N_SPACES, len(FIELD_CARD_COLS)), dtype=np.float32)
    for k, space in enumerate(BUYABLE_LIST):
        s = BOARD[space]
        row = t[k]
        row[c["price"]] = scale(s.price, CARD_S)
        row[c["house_cost"]] = scale(s.house_cost, CARD_S)
        row[c["mortgage"]] = scale(s.mortgage_value, CARD_S)
        row[c["unmortgage"]] = scale(unmortgage_cost(space), CARD_S)
        if s.kind is Kind.PROPERTY:
            base = s.rents[0]
            for name, rent in zip(("rent", "set_rent", "house1", "house2", "house3", "house4", "hotel"),
                                  (base, 2 * base, *s.rents[1:])):
                row[c[name]] = scale(rent, CARD_S)
            row[c[s.group]] = 1.0
        elif s.kind is Kind.RAILROAD:
            for j, rent in enumerate(RAILROAD_RENTS):
                row[c[f"stations{j + 1}"]] = scale(rent, CARD_S)
            row[c["station"]] = 1.0
        else:
            row[c["utility_x4"]], row[c["utility_x10"]] = (math.log1p(m) for m in UTILITY_MULTIPLIERS)
            row[c["utility"]] = 1.0
    return t

Z_PLAYERS = 0
Z_WINDOWS = 3
Z_TRADES = Z_WINDOWS + len(TRADE_WINDOW_LEVELS)
Z_EXCHANGES = Z_TRADES + 1
Z_LIMIT, Z_LIMIT_ROUNDS, Z_LEFT = Z_EXCHANGES + 1, Z_EXCHANGES + 2, Z_EXCHANGES + 3
RULES_SIZE = Z_LEFT + 1

def _fill_rules(v: np.ndarray, o: int, ctx: _Ctx, params: dict) -> None:
    state = ctx.state
    cfg = state.config
    v[o + Z_PLAYERS + ctx.n - 2] = 1.0
    v[o + Z_WINDOWS + TRADE_WINDOW_LEVELS.index(cfg.trade_windows)] = 1.0
    v[o + Z_TRADES] = cfg.max_trades_per_turn / 3.0
    v[o + Z_EXCHANGES] = cfg.max_trade_exchanges / 4.0
    if cfg.max_rounds:
        v[o + Z_LIMIT] = 1.0
        v[o + Z_LIMIT_ROUNDS] = scale(cfg.max_rounds, ROUND_S)
        v[o + Z_LEFT] = scale(max(cfg.max_rounds - state.round, 0), ROUND_S)

C_OFFERS, C_ACCEPTED, C_REJECTED, C_COUNTERED, C_PAID = range(5)
C_DIR = 5
C_PAIR_OUT, C_PAIR_IN = 0, C_DIR
C_PAIR_PER = 2 * C_DIR
C_EACH0 = (SLOTS - 1) * C_PAIR_PER
E_WON, E_AUCTION_PAID, E_DECLINED, E_PROPOSALS, E_ACCEPTED, E_REJECTED = range(6)
C_EACH = 6
C_CARDS0 = C_EACH0 + SLOTS * C_EACH
DECK_NAMES = ("opportunity", "community_fund")
C_DRAWN = C_CARDS0 + len(DECK_NAMES) * DECK_SIZE
LICZNIKI_SIZE = C_DRAWN + len(DECK_NAMES)

def _fill_counts(v: np.ndarray, o: int, ctx: _Ctx, m) -> None:

    player, n = ctx.player, ctx.n
    pairs = bool(m.accepted_pair)
    for slot in range(1, n):
        r = (player + slot) % n
        for d, (a, b) in enumerate(((player, r), (r, player))):
            p = o + (slot - 1) * C_PAIR_PER + d * C_DIR
            v[p + C_OFFERS] = scale(m.offers[a][b], COUNT_S) if m.offers else 0.0
            v[p + C_REJECTED] = scale(m.rejects[a][b], COUNT_S) if m.rejects else 0.0
            if pairs:
                v[p + C_ACCEPTED] = scale(m.accepted_pair[a][b], COUNT_S)
                v[p + C_COUNTERED] = scale(m.countered[a][b], COUNT_S)
            v[p + C_PAID] = scale(m.paid[a][b], CASH_S)
    for slot in range(n):
        pid = (player + slot) % n
        p = o + C_EACH0 + slot * C_EACH
        v[p + E_WON] = scale(m.auctions_won[pid], COUNT_S)
        v[p + E_AUCTION_PAID] = scale(m.auction_paid[pid], CASH_S)
        v[p + E_DECLINED] = scale(m.declined[pid], COUNT_S) if m.declined else 0.0
        v[p + E_PROPOSALS] = scale(m.proposals[pid], COUNT_S)
        v[p + E_ACCEPTED] = scale(m.accepted[pid], COUNT_S)
        v[p + E_REJECTED] = scale(m.rejected[pid], COUNT_S)

def _parts_counters(params: dict) -> list[Part]:
    return [Part("player", 0, C_PAIR_PER, C_PAIR_PER, first=1), Part("player", C_EACH0, C_EACH, C_EACH)]

OUTCOMES = ("accepted", "rejected", "countered", "pending")
VOCAB = {"kind": len(LOG_KINDS) + 1, "who": SLOTS + 1, "space": 41, "card": len(DECK_NAMES) * DECK_SIZE + 1,
         "outcome": len(OUTCOMES) + 1, "turn_kind": len(TURN_KINDS) + 1, "origin": len(DRAFT_ORIGINS) + 1,
         "edit_kind": len(STEP_KINDS) + 1}
_GIVE = [f"give{i}" for i in range(LOG_SPACES)]
_GET = [f"get{i}" for i in range(LOG_SPACES)]
_BIDS = [f"bids{i}" for i in range(SLOTS)]
EVENT_INDEX = ["kind", "who", "to", "space", "card", *_GIVE, *_GET]
EVENT_CONT = ["truncated", *_BIDS, "amount", "age"]
PAIR_INDEX = [*_GIVE, *_GET, "outcome"]
PAIR_CONT = ["truncated", "cash", "cards_give", "cards_get", "age", "counter"]
EVENT_STRIDE = len(EVENT_INDEX) + len(EVENT_CONT)
PAIR_STRIDE = len(PAIR_INDEX) + len(PAIR_CONT)

TURN_INDEX = ["turn_kind", "to", "space", *_GIVE, *_GET, "outcome", "origin"]
TURN_CONT = ["truncated", "amount", "cards_give", "cards_get", "steps"]
TURN_STRIDE = len(TURN_INDEX) + len(TURN_CONT)

STEP_INDEX = ["edit_kind", "space"]
STEP_CONT = ["sign", "amount", "net"]
STEP_STRIDE = len(STEP_INDEX) + len(STEP_CONT)
_COL_VOCAB = {"kind": "kind", "who": "who", "to": "who", "space": "space", "card": "card", "outcome": "outcome",
              **{c: "space" for c in _GIVE + _GET}, "turn_kind": "turn_kind", "origin": "origin", "edit_kind": "edit_kind",

              **{f"{side}{i}": "space" for side in ("up", "down") for i in range(LOG_SPACES)},
              "direction": "direction", "phase": "phase", "window": "window", "window_actor": "who"}
_KIND_IDX = {k: i + 1 for i, k in enumerate(LOG_KINDS)}
_OUTCOME_IDX = {k: i + 1 for i, k in enumerate(OUTCOMES)}
_DECK_OFF = {deck: k * DECK_SIZE for k, deck in enumerate(DECK_NAMES)}

@dataclass
class EntryGroup:

    kind: str
    base: int
    n: int
    stride: int
    index_cols: dict[str, int]
    cont_cols: dict[str, int]
    key: str

def _cols(index: list[str], cont: list[str]) -> tuple[dict[str, int], dict[str, int]]:
    return {c: k for k, c in enumerate(index)}, {c: len(index) + k for k, c in enumerate(cont)}

@dataclass
class EntryFields:

    event0: int = 0
    event_n: int = 0
    event_stride: int = EVENT_STRIDE
    event_index_cols: dict[str, int] = field(default_factory=lambda: {c: k for k, c in enumerate(EVENT_INDEX)})
    event_cont_cols: dict[str, int] = field(
        default_factory=lambda: {c: len(EVENT_INDEX) + k for k, c in enumerate(EVENT_CONT)})
    pair0: int = 0
    pair_n: int = 0
    pair_stride: int = PAIR_STRIDE
    pair_index_cols: dict[str, int] = field(default_factory=lambda: {c: k for k, c in enumerate(PAIR_INDEX)})
    pair_cont_cols: dict[str, int] = field(
        default_factory=lambda: {c: len(PAIR_INDEX) + k for k, c in enumerate(PAIR_CONT)})
    pair_k: int = 0
    vocab: dict[str, int] = field(default_factory=lambda: dict(VOCAB))
    extra: list[EntryGroup] = field(default_factory=list)

    def groups(self) -> list[EntryGroup]:

        return [EntryGroup("event", self.event0, self.event_n, self.event_stride, self.event_index_cols,
                           self.event_cont_cols, "kind"),
                EntryGroup("pair", self.pair0, self.pair_n, self.pair_stride, self.pair_index_cols, self.pair_cont_cols,
                           "outcome"),
                *self.extra]

    @staticmethod
    def vocab_of(col: str) -> str:

        return _COL_VOCAB[col]

    def index_positions(self) -> dict[str, np.ndarray]:

        out: dict[str, list[int]] = {v: [] for v in self.vocab}
        for g in self.groups():
            for k in range(g.n):
                for col, c in g.index_cols.items():
                    out[_COL_VOCAB[col]].append(g.base + k * g.stride + c)
        return {v: np.array(sorted(ix), np.int64) for v, ix in out.items()}

def _space_idx(space) -> int:
    return 0 if space is None else space + 1

_MGMT = frozenset(MANAGEMENT)
_UP = frozenset(("build", "unmortgage"))
_SIGN = {"build": -1, "sell_building": 1, "mortgage": 1, "unmortgage": -1}
_COUNT_COL = {k: i for i, k in enumerate(MANAGEMENT)}

def compact_log(log, limit: int) -> list[tuple]:

    out: list[tuple] = []
    i = len(log) - 1
    while i >= 0 and len(out) < limit:
        e = log[i]
        j = i
        if e.kind in _MGMT:
            while j > 0:
                p = log[j - 1]
                if p.kind not in _MGMT or p.who != e.who or p.round != e.round or p.turn != e.turn:
                    break
                j -= 1
        out.append(tuple(log[j : i + 1]))
        i = j - 1
    return out

@dataclass(frozen=True)
class InputBlock:

    desc: str
    size: Callable[[dict], int]
    fill: Callable | None
    parts: Callable[[dict], list[Part]] = lambda p: []
    default: int | None = None
    maximum: int | None = None
    excludes: tuple[str, ...] = ()
    optional: bool = False

_ORIGIN_IDX = {k: i + 1 for i, k in enumerate(DRAFT_ORIGINS)}
_STEP_KIND_IDX = {k: i + 1 for i, k in enumerate(STEP_KINDS)}
_UTILITY_POS = [BUYABLE_POS[s.index] for s in BOARD if s.kind is Kind.UTILITY]
