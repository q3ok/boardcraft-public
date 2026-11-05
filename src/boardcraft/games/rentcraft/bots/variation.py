"""Seeded variation in proposal rankings, prices, thresholds and legal actions."""
import math
import random

from ..board import BOARD
from ..actions import Action, ActionKind, Decision
from ..state import GameState
from ..trade import TradeOffer, validate_offer


CASH_SHARE = 0.2

RANK_TEMP = 3.0


def new_rng(seed: int) -> random.Random:
    return random.Random(seed * 1_000_003 + 0x5EED)


def pick(rng: random.Random, n: int, z: float) -> int:
    if n <= 1 or z <= 0:
        return 0
    tau = z * RANK_TEMP
    weights = [math.exp(-k / tau) for k in range(n)]
    r = rng.random() * sum(weights)
    for k, w in enumerate(weights):
        r -= w
        if r < 0:
            return k
    return n - 1


def jitter_cash(rng: random.Random, amount: int, z: float) -> int:
    if amount <= 0 or z <= 0:
        return amount
    span = z * CASH_SHARE * amount
    return max(10, int(round((amount + rng.uniform(-span, span)) / 10)) * 10)


def jitter_offer(rng: random.Random, offer: TradeOffer, z: float) -> TradeOffer:
    if z <= 0 or not (offer.give_cash or offer.get_cash):
        return offer
    return TradeOffer(offer.proposer, offer.target, give_spaces=offer.give_spaces, get_spaces=offer.get_spaces,
                      give_cash=jitter_cash(rng, offer.give_cash, z), get_cash=jitter_cash(rng, offer.get_cash, z),
                      give_cards=offer.give_cards, get_cards=offer.get_cards)


def nominal(offer: TradeOffer) -> float:
    spaces = sum(BOARD[s].price for s in (*offer.give_spaces, *offer.get_spaces))
    return float(spaces + offer.give_cash + offer.get_cash + 50 * (offer.give_cards + offer.get_cards))


def threshold(rng: random.Random, offer: TradeOffer, z: float) -> float:
    if z <= 0:
        return 0.0
    return rng.uniform(-1.0, 1.0) * z * CASH_SHARE * nominal(offer)


def varied(state: GameState, rng: random.Random, offer: TradeOffer, z: float, ok) -> TradeOffer:
    new = jitter_offer(rng, offer, z)
    if new == offer:
        return offer
    if validate_offer(state, new) is None and ok(new):
        return new
    return offer


def random_action(state: GameState, d: Decision, rng: random.Random, refuse) -> Action | None:
    acts = [a for a in d.actions if not refuse(a)]
    if len(acts) > 1:
        acts = [a for a in acts if a.kind is not ActionKind.DECLARE_BANKRUPTCY] or acts
    return rng.choice(acts) if acts else None
