"""Adapt public observations to rule players and checkpoint each seat independently."""
import json
import random
from types import UnionType
from typing import NewType, get_args, get_origin

from boardcraft.contracts import Action, BotChoice, Observation, _copy_json
from ..actions import ActionKind, Phase
from ..engine import public_action
from ..observation import hypothetical_game
from ..persistence import decode, plain
from ..trade import TradeOffer
from . import BEHAVIOR_VERSION
from .settings import configured_strategy

Pair = tuple[int, int]
Spaces = tuple[int, ...]
Groups = tuple[str, ...]
OfferKey = tuple[int, Spaces, Spaces]
NegotiationSignature = tuple[Spaces, Spaces, Groups, Groups, bool, bool]
Count = NewType("Count", int)
Negotiation = tuple[NegotiationSignature, int, int, Count, set[TradeOffer]]

# Derived valuation caches can always be recomputed from the current public board.
# Include the independent random stream and proposal history of every delegated player.
FIELDS = {
    "rng": random.Random, "_rng": random.Random, "_variation_rng": random.Random,
    "_tried": set[TradeOffer], "_tried_owners": tuple[int | None, ...],
    "_raising": Pair | None, "_win_slot": Pair | None, "_win_steps": Count,
    "_proposed_turn": int, "_last_why": str, "_attempts": dict[Pair, Count],
    "_auction_limit": dict[int, tuple[int, str]], "_sent": Spaces,
    "_why": tuple[tuple[TradeOffer, str], ...], "_blocked": list[str],
    "_absurd_sent": tuple[int, set[str]], "_auction": dict[Pair, tuple[int, str]],
    "_neg": dict[OfferKey, Negotiation], "_mort_round": dict[int, Count],
    "_slot": tuple[int, Phase, int, int, Count, Count] | None, "_slot_steps": Count,
    "_buying": Pair | None, "_round": Count, "_rescue_tried": set[TradeOffer],
}


def pack(value):
    if isinstance(value, random.Random):
        return plain(value.getstate())
    if isinstance(value, dict):
        return [[pack(k), pack(v)] for k, v in value.items()]
    if isinstance(value, set):
        return sorted((pack(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    if isinstance(value, (tuple, list)):
        return [pack(v) for v in value]
    return plain(value)


def unpack(kind, data):
    origin, args = get_origin(kind), get_args(kind)
    if kind is Count:
        if type(data) is not int or data < 0:
            raise ValueError("Bot memory counters must be nonnegative integers")
        return data
    if kind is random.Random:
        version, numbers, spare = decode(tuple[int, tuple[int, ...], type(None)], data)
        if (version != 3 or len(numbers) != 625 or not 0 <= numbers[-1] <= 624
                or any(not 0 <= word < 2**32 for word in numbers[:-1]) or not any(numbers[:-1])):
            raise ValueError("Invalid bot random generator state")
        rng = random.Random(0)
        rng.setstate((version, numbers, spare))
        return rng
    if origin is UnionType:
        return None if data is None else unpack(next(t for t in args if t is not type(None)), data)
    if origin is dict:
        key_type, value_type = args
        rows = unpack(list[tuple[key_type, value_type]], data)
        result = dict(rows)
        if len(rows) != len(result):
            raise ValueError("Duplicate bot memory key")
        return result
    if origin in (tuple, list, set):
        if type(data) is not list:
            raise ValueError("Expected an array in bot memory")
        if origin is tuple and args[-1] is not Ellipsis:
            if len(data) != len(args):
                raise ValueError("Incorrect bot memory tuple length")
            return tuple(unpack(t, v) for t, v in zip(args, data))
        result = origin(unpack(args[0], v) for v in data)
        if len(result) != len(data):
            raise ValueError("Duplicate bot memory element")
        return result
    return decode(kind, data)


def memory_fields(strategy):
    return {name: kind for name, kind in FIELDS.items() if name in vars(strategy)}


def checkpoint(strategy):
    return {"fields": {k: pack(getattr(strategy, k)) for k in memory_fields(strategy)},
            "delegates": [checkpoint(bot) for bot, _ in getattr(strategy, "_delegates", ())]}


def restore(strategy, data):
    shape = memory_fields(strategy)
    delegates = getattr(strategy, "_delegates", ())
    if (type(data) is not dict or set(data) != {"fields", "delegates"} or type(data["fields"]) is not dict
            or shape.keys() != data["fields"].keys() or type(data["delegates"]) is not list
            or len(delegates) != len(data["delegates"])):
        raise ValueError("Invalid rule-player memory shape")
    for name, kind in shape.items():
        setattr(strategy, name, unpack(kind, data["fields"][name]))
    for (bot, _), saved in zip(delegates, data["delegates"]):
        restore(bot, saved)


class RentcraftBot:
    def __init__(self, kind, seed, options):
        if type(seed) is not int:
            raise ValueError("Bot seeds must be integers")
        self.kind = kind
        self._strategy, self._options = configured_strategy(kind, seed, options)
        self._world_rng = random.Random(seed)

    def choose(self, observation: Observation, legal_actions: tuple[Action, ...]) -> BotChoice:
        public = observation.decision
        if public is None or public.player != observation.viewer:
            raise ValueError("A rule player needs its own current decision")
        game = hypothetical_game(observation, self._world_rng.getrandbits(64))
        decision = game.decision()
        if (decision is None or decision.player != public.player or decision.phase.value != public.kind
                or tuple(public_action(a) for a in decision.actions) != tuple(legal_actions)):
            raise ValueError("Decision and legal actions do not match the observed board")
        choice = self._strategy.choose(game.state, decision)
        action = public_action(choice.action)
        if action not in legal_actions and choice.action.kind not in (ActionKind.PROPOSE_TRADE, ActionKind.COUNTER_TRADE):
            raise ValueError("Rule player selected an unavailable action")
        return BotChoice(action, choice.explanation)

    def save_state(self) -> dict:
        return {"version": BEHAVIOR_VERSION, "kind": self.kind, "options": json.loads(json.dumps(self._options)),
                "world_rng": pack(self._world_rng), "memory": checkpoint(self._strategy)}

    def load_state(self, state: dict) -> None:
        if (type(state) is not dict or set(state) != {"version", "kind", "options", "world_rng", "memory"}
                or state["version"] != BEHAVIOR_VERSION or state["kind"] != self.kind
                or json.dumps(_copy_json(state["options"]), sort_keys=True) != json.dumps(self._options, sort_keys=True)):
            raise ValueError("Bot memory does not match the selected player")
        # Decode into a fresh instance so a malformed nested field cannot partially update this seat.
        candidate, _ = configured_strategy(self.kind, 0, self._options)
        world_rng = unpack(random.Random, state["world_rng"])
        restore(candidate, state["memory"])
        self._strategy, self._world_rng = candidate, world_rng

