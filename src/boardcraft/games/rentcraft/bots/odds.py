"""Landing frequencies from repeated dice rolls, card effects and time spent in jail."""
from functools import lru_cache

from ..board import BOARD, GO_TO_JAIL_INDEX, Kind, RAILROADS, UTILITIES
from ..cards import DECKS

JAIL_STATE = 40
_JAIL_SQUARE = 10


def roll_probs() -> dict[int, float]:
    out: dict[int, float] = {}
    for a in range(1, 7):
        for b in range(1, 7):
            out[a + b] = out.get(a + b, 0.0) + 1 / 36
    return out


def _nearest(pos: int, targets: tuple[int, ...]) -> int:
    return min(targets, key=lambda t: (t - pos) % 40)


def _resolve(pos: int, depth: int = 0) -> dict[int, float]:
    if pos == GO_TO_JAIL_INDEX:
        return {JAIL_STATE: 1.0}
    kind = BOARD[pos].kind
    if kind not in (Kind.OPPORTUNITY, Kind.COMMUNITY_FUND) or depth > 1:
        return {pos: 1.0}
    deck = DECKS["opportunity"] if kind is Kind.OPPORTUNITY else DECKS["community_fund"]
    out: dict[int, float] = {}

    def add(dist: dict[int, float], w: float) -> None:
        for k, v in dist.items():
            out[k] = out.get(k, 0.0) + v * w

    w = 1 / len(deck)
    for card in deck:
        if card.effect == "advance_to":
            add({card.target: 1.0}, w)
        elif card.effect == "advance_nearest_railroad":
            add({_nearest(pos, RAILROADS): 1.0}, w)
        elif card.effect == "advance_nearest_utility":
            add({_nearest(pos, UTILITIES): 1.0}, w)
        elif card.effect == "back_three":
            add(_resolve((pos - 3) % 40, depth + 1), w)
        elif card.effect == "go_to_jail":
            add({JAIL_STATE: 1.0}, w)
        else:
            add({pos: 1.0}, w)
    return out


@lru_cache(maxsize=1)
def landing() -> tuple[float, ...]:
    rolls = roll_probs()
    n = 41
    trans = [[0.0] * n for _ in range(n)]
    for s in range(n):
        pos = _JAIL_SQUARE if s == JAIL_STATE else s
        for d, pd in rolls.items():
            for t, pt in _resolve((pos + d) % 40).items():
                trans[s][t] += pd * pt
    p = [1.0 / n] * n
    for _ in range(500):
        q = [0.0] * n
        for s in range(n):
            if p[s]:
                row = trans[s]
                for t in range(n):
                    if row[t]:
                        q[t] += p[s] * row[t]
        p = q
    return tuple(p)
