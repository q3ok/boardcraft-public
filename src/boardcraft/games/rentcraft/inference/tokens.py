"""Local token and embedding index maps; no shared Dice layout changes."""
from dataclasses import dataclass

import numpy as np

from .encoding_blocks import BUYABLE_LIST, SLOTS

N_SPACES = len(BUYABLE_LIST)

@dataclass
class TokenLayout:
    size: int
    pad: int
    player_idx: np.ndarray
    space_idx: np.ndarray
    globals: list[tuple[str, np.ndarray]]

    @property
    def n_tokens(self) -> int:
        return SLOTS + N_SPACES + len(self.globals)


def place_parts(block: str, o: int, n: int, parts, pad: int, players: list[list[int]],
                spaces: list[list[int]]) -> set[int]:

    used: set[int] = set()
    for part in parts:
        rows, count = (players, SLOTS) if part.kind == "player" else (spaces, N_SPACES)
        for f in range(part.feats):
            for i in range(count):
                if i < part.first:
                    rows[i].append(pad)
                    continue
                k = o + part.start + (i - part.first) * part.item_stride + f * part.feat_stride
                if not o <= k < o + n or k in used:
                    raise ValueError(f"Block {block} has an out-of-range or overlapping index {k}")
                used.add(k)
                rows[i].append(k)
    return used


from dataclasses import field
from .entries import EntryFields, field_card_table

@dataclass
class EntryTokenLayout(TokenLayout):
    fields: EntryFields
    field_card: np.ndarray | None
    event_idx: np.ndarray
    pair_idx: np.ndarray

    entry_idx: dict[str, np.ndarray] = field(default_factory=dict)

    @property
    def n_tokens(self) -> int:
        return SLOTS + N_SPACES + len(self.globals) + sum(g.n for g in self.fields.groups())

def _entries(base: int, n: int, stride: int) -> np.ndarray:
    return (base + np.arange(n, dtype=np.int64)[:, None] * stride + np.arange(stride, dtype=np.int64)).reshape(n, stride)

def _entry_layout(encoder: str) -> EntryTokenLayout:

    from .profiles import resolve_profile

    enc = resolve_profile(encoder).encoder()
    size, f = enc.size, enc.fields
    players: list[list[int]] = [[] for _ in range(SLOTS)]
    spaces: list[list[int]] = [[] for _ in range(N_SPACES)]
    globs: list[tuple[str, np.ndarray]] = []
    entry_idx = {g.kind: _entries(g.base, g.n, g.stride) for g in f.groups()}
    in_entries = np.zeros(size, bool)
    for idx in entry_idx.values():
        in_entries[idx.ravel()] = True
    for block, (o, n) in enc.layout.items():
        if block in enc.entry_blocks:
            continue
        used = place_parts(block, o, n, enc.parts(block), size, players, spaces)
        rest = [k for k in range(o, o + n) if k not in used and not in_entries[k]]
        if rest:
            globs.append((block, np.array(rest, np.int64)))
    return EntryTokenLayout(size, size, np.array(players, np.int64).reshape(SLOTS, -1),
                         np.array(spaces, np.int64).reshape(N_SPACES, -1), globs, f,
                         field_card_table() if enc.field_card else None,
                         entry_idx["event"], entry_idx["pair"], entry_idx)

def emb_table(col: str) -> str:

    return col.rstrip("0123456789") if col.startswith(("give", "get", "up", "down")) else col


def tensor_table(table: str) -> str:
    """Keep legacy archive tensor names while encoder fields use English names."""
    return {"direction": "kierunek", "edit_kind": "krok", "turn_kind": "tura_rodzaj",
            "origin": "zrodlo", "phase": "faza", "window": "okno", "window_actor": "okno_kto"}.get(table, table)

def index_tables(f: EntryFields) -> list[tuple[str, int, np.ndarray]]:

    out: dict[str, tuple[int, list[int]]] = {}
    for g in f.groups():
        if not g.n:
            continue
        for col in g.index_cols:
            out.setdefault(emb_table(col), (f.vocab[f.vocab_of(col)], []))
        for k in range(g.n):
            for col, c in g.index_cols.items():
                out[emb_table(col)][1].append(g.base + k * g.stride + c)
    return [(t, vocab, np.array(ix, np.int64)) for t, (vocab, ix) in out.items()]


def token_layout(encoder):
    from .profiles import MINI, V7, resolve_profile
    if encoder == V7:
        return _entry_layout(encoder)
    enc = resolve_profile(encoder).encoder()
    # Legacy mini token partition, retained for archive shape validation.
    from .encoding_blocks import Part
    parts = {
        "state": [Part("player", 8, 47, 47), Part("space", 196, 7, 7),
                  Part("space", 392, 1, 1), Part("space", 420, 1, 1), Part("space", 454, 2, 2)],
        "context": [Part("player", 0, 2, 1, 4), Part("player", 9, 2, 1, 4)],
        "memory": [Part("player", 0, 7, 7)],
        "rents": [Part("space", 0, 5, 5)],
        "complete_sets": [Part("player", 0, 8, 8)],
        "debts": [Part("player", 0, 1, 1)],
        "trade_history": [Part("player", 0, 8, 8, first=1)],
        "liquidity": [Part("player", 0, 2, 2)],
        "draft": [Part("player", 2, 1, 1), Part("space", 6, 2, 2)],
        "changes": [Part("player", 0, 7, 7)],
    }
    players, spaces, globs = [[] for _ in range(SLOTS)], [[] for _ in range(N_SPACES)], []
    for block, (o, n) in enc.layout.items():
        used = place_parts(block, o, n, parts.get(block, ()), enc.size, players, spaces)
        rest = [k for k in range(o, o+n) if k not in used]
        if rest:
            globs.append((block, np.array(rest, np.int64)))
    return TokenLayout(enc.size, enc.size, np.array(players, np.int64), np.array(spaces, np.int64), globs)
