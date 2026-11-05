"""Two decks with public card identities and private draw order."""
import json
from dataclasses import dataclass

from .board import resource
from .state import GameState


@dataclass(frozen=True)
class Card:
    id: int
    deck: str
    text: str
    effect: str
    value: int
    value2: int
    target: int | None


DECKS = {name: tuple(Card(**item) for item in rows)
         for name, rows in json.loads(resource("data/cards.en.json")).items()}
JAIL_FREE_ID = {name: next(c.id for c in cards if c.effect == "jail_free") for name, cards in DECKS.items()}


def draw_card(state: GameState, deck: str) -> Card:
    order = state.decks[deck]
    card = DECKS[deck][order.pop(0)]
    if card.effect != "jail_free":
        order.append(card.id)
    return card

