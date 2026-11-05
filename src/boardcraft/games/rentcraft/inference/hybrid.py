"""Observation-only combined player with independently pinned component state."""
from copy import copy, deepcopy
import inspect
import json

from boardcraft.contracts import RuleBotReference
from boardcraft.inference.spec import digest
from ..actions import Phase
from .bot import RentcraftInferenceBot

PLAYER_AREAS = ("buy", "auctions", "management", "offers", "responses")
_PLAYER_BY_PHASE = {Phase.BUY_DECISION: "buy", Phase.AUCTION: "auctions",
                    Phase.TRADE_RESPONSE: "responses", Phase.TRADE_WINDOW: "offers"}
PREFIX = "hybrid:"
MAX_SPEC = 8192
_PROPOSALS = ("propose_trade", "counter_trade")


def is_hybrid(spec):
    return isinstance(spec, str) and spec.strip().startswith(PREFIX)


def parse_hybrid(spec):
    if not isinstance(spec, str) or len(spec) > MAX_SPEC or not is_hybrid(spec):
        raise ValueError("A combined player must start with hybrid:")
    given = {}
    for entry in spec.strip()[len(PREFIX):].split(";"):
        if not entry.strip():
            continue
        if "=" not in entry:
            raise ValueError("Expected area=player in combined player")
        area, player = (part.strip() for part in entry.split("=", 1))
        area = area.lower()
        if area not in (*PLAYER_AREAS, "rest") or area in given:
            raise ValueError("Unknown or duplicate combined-player area")
        if not player or is_hybrid(player):
            raise ValueError("Missing player or nested combined player")
        given[area] = player
    rest = given.pop("rest", None)
    owners = {area: given.get(area, rest) for area in PLAYER_AREAS}
    if any(player is None for player in owners.values()):
        raise ValueError("Every combined-player area needs an owner or rest")
    return owners


def component_references(reference):
    """Decode the closed, nonnested immutable composition stored in a rule pin."""
    from boardcraft.inference.catalog import reference_from_data, rule_compatibility
    from boardcraft.registry import get_game
    if (not isinstance(reference, RuleBotReference) or reference.kind != "builtin"
            or reference.builtin_kind != "hybrid" or reference.behavior_version != "1"
            or reference.compatibility != rule_compatibility(get_game("rentcraft"))):
        raise ValueError("Invalid combined-player reference")
    data = json.loads(reference.options_json)
    if type(data) is not dict or set(data) != {"owners"} or type(data["owners"]) is not dict or set(data["owners"]) != set(PLAYER_AREAS):
        raise ValueError("Invalid combined-player owners")
    result = {}
    for area in PLAYER_AREAS:
        row = data["owners"][area]
        if not isinstance(row, dict) or row.get("builtin_kind") == "hybrid":
            raise ValueError("Nested combined-player reference")
        child = reference_from_data(row)
        if child.compatibility.game_id != "rentcraft":
            raise ValueError("Combined players require Rentcraft components")
        result[area] = child
    return result


def hybrid_reference(module, owners):
    from boardcraft.inference.catalog import reference_data, rule_compatibility, _options_json, _payload
    options = _options_json({"owners": {area: reference_data(owners[area]) for area in PLAYER_AREAS}})
    kwargs = dict(builtin_kind="hybrid", behavior_version="1", options_json=options)
    ref = RuleBotReference("builtin", "", rule_compatibility(module), **kwargs)
    component_references(ref)
    return RuleBotReference("builtin", digest(_payload(ref)), ref.compatibility, **kwargs)


class HybridPlayer:
    def __init__(self, owners, *, binding=None, component_ids=None):
        if type(owners) is not dict or set(owners) != set(PLAYER_AREAS):
            raise ValueError("Every combined-player area needs exactly one owner")
        self.owners = {area: owners[area] for area in PLAYER_AREAS}
        self._binding = binding
        self._components = {}
        self._keys = {}
        for area, owner in self.owners.items():
            key = component_ids[area] if component_ids is not None else str(id(owner))
            self._components.setdefault(key, owner)
            self._keys[area] = key

    def choose(self, observation, legal_actions):
        area = _PLAYER_BY_PHASE.get(Phase(observation.decision.kind), "management")
        owner, offerer = self.owners[area], self.owners["offers"]
        if area in ("offers", "responses") or owner is offerer:
            return self._choose(owner, observation, legal_actions)
        offered = self._choose(offerer, observation, legal_actions)
        if offered.action.kind in _PROPOSALS:
            return offered
        choice = self._choose(owner, observation, legal_actions, allow_trades=False)
        return offered if choice.action.kind in _PROPOSALS else choice

    def _choose(self, owner, observation, legal_actions, *, allow_trades=True):
        if not allow_trades and "allow_trades" in inspect.signature(owner.choose).parameters:
            choice = owner.choose(observation, legal_actions, allow_trades=False)
        else:
            choice = owner.choose(observation, legal_actions)
        # The original players see one seat's shared memory, including notes from
        # an offers-owner decision whose final nontrade move is discarded.
        if isinstance(owner, RentcraftInferenceBot):
            for component in self._components.values():
                if component is not owner and isinstance(component, RentcraftInferenceBot):
                    component._notes = deepcopy(owner._notes)
        return choice

    def _candidates(self):
        result = {}
        for key, owner in self._components.items():
            candidate = copy(owner)
            candidate.load_state(deepcopy(owner.save_state()))
            result[key] = candidate
        return result

    def _install(self, components):
        self._components = components
        self.owners = {area: components[key] for area, key in self._keys.items()}

    def initialize(self, observation):
        components = self._candidates()
        for component in components.values():
            initialize = getattr(component, "initialize", None)
            if callable(initialize):
                initialize(observation)
        self._install(components)

    def save_state(self):
        return {"version": 1, "binding": self._binding,
                "components": {key: deepcopy(owner.save_state()) for key, owner in self._components.items()}}

    def load_state(self, state):
        if (type(state) is not dict or set(state) != {"version", "binding", "components"}
                or type(state["version"]) is not int or state["version"] != 1
                or state["binding"] != self._binding or type(state["components"]) is not dict
                or set(state["components"]) != set(self._components)):
            raise ValueError("Checkpoint does not match the combined player")
        components = self._candidates()
        for key, component in components.items():
            component.load_state(deepcopy(state["components"][key]))
        seats = {saved["seat"] for component in components.values()
                 for saved in (component.save_state(),) if "seat" in saved}
        if len(seats) > 1:
            raise ValueError("Combined-player components belong to different seats")
        notes = [component.save_state()["notes"] for component in components.values()
                 if isinstance(component, RentcraftInferenceBot)]
        if notes and any(note != notes[0] for note in notes[1:]):
            raise ValueError("Combined-player components disagree on shared seat notes")
        self._install(components)
