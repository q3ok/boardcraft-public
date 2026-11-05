"""Greedy observation-only Rentcraft player with seat-local, note-only memory."""
from copy import deepcopy
from dataclasses import dataclass, replace
from types import SimpleNamespace

from boardcraft.contracts import BotChoice
from boardcraft.inference.identity import canonical_tensor_digest
from boardcraft.inference.spec import digest
from ..engine import public_action
from ..memory import ModelAct, ModelHistory
from ..observation import require_model_context
from ..persistence import decode, plain, _validate_model_history
from ..actions import ActionKind, Phase
from ..text import describe_action
import numpy as np
from .profiles import resolve_profile
from .policy import NumpyPolicy
from .tradebuilder import DecisionGame, NoteOverlay, PrivateAct, DRAFT_ORIGINS, choose_index, without_trade_starts


@dataclass
class _Notes:
    turn: int
    accepted: list[ModelAct]
    accepted_ids: list[int]
    sequence: int
    records: list[tuple[int | None, PrivateAct]]
    pending: tuple[int, tuple[int, int, tuple[int, ...], tuple[int, ...], int, int, int, int], str, int] | None
    final_kind: str | None
    revision: int | None


_FIELDS = tuple(_Notes.__annotations__)
_ALIASES = {"decline_buy": "decline", "pay_jail_fine": "jail_fine", "offer": "trade_offer",
            "counter_offer": "trade_counter", "pay_interest": "interest"}


def _restore_notes(seat, data):
    notes = decode(_Notes, data)
    if (notes.turn != -1 and notes.turn < 1 or notes.sequence < 0
            or len(notes.accepted) > 64 or len(notes.records) > 64
            or len(notes.accepted_ids) != len(notes.accepted)
            or notes.accepted_ids != sorted(set(notes.accepted_ids))
            or any(not 0 < i <= notes.sequence for i in notes.accepted_ids)
            or notes.turn >= 1 and notes.revision is None
            or notes.revision is not None and notes.revision < 0
            or notes.final_kind not in (None, *[a.value for a in ActionKind], "offer", "counter_offer",
                                        "decline_buy", "pass_window", "pay_interest")):
        raise ValueError("Invalid model note checkpoint")
    ids = [i for i, _ in notes.records if i is not None]
    expected_ids = notes.accepted_ids[-len(ids):] if ids else []
    if (ids != expected_ids or len(notes.records) < 64 and ids != notes.accepted_ids
            or ids != sorted(set(ids))):
        raise ValueError("Invalid model note order")
    if any(a.kind == "abandoned_draft" for a in notes.accepted):
        raise ValueError("Accepted notes cannot contain abandoned drafts")
    accepted = dict(zip(notes.accepted_ids, notes.accepted))
    rows = list(notes.accepted)
    for index, act in notes.records:
        base = ModelAct(**{k: v for k, v in vars(act).items() if k not in ("origin", "steps")})
        if ((index is None) != (act.kind == "abandoned_draft")
                or index is not None and base != accepted[index]
                or act.kind == "abandoned_draft" and act.outcome):
            raise ValueError("Private notes disagree with accepted history")
        if (act.origin not in ("", *DRAFT_ORIGINS) or not 0 <= act.steps <= 16
                or act.kind == "abandoned_draft" and not act.origin
                or act.kind not in ("offer", "counter_offer", "abandoned_draft") and (act.origin or act.steps)):
            raise ValueError("Invalid private model note")
        rows.append(base)
    for row in rows:
        if row.who != seat or row.turn != notes.turn:
            raise ValueError("Model notes belong to a different seat or turn")
        row = replace(row, kind=_ALIASES.get(row.kind, row.kind))
        if row.kind == "abandoned_draft":
            row = replace(row, kind="trade_offer", outcome="pending")
        history = ModelHistory(own_acts=[row])
        state = SimpleNamespace(players=[None] * 4, current=seat, turn=notes.turn,
                                memory=SimpleNamespace(model_history=history))
        _validate_model_history(state)
    if notes.pending is not None:
        turn, key, origin, steps = notes.pending
        who, target, give, get, give_cash, get_cash, give_cards, get_cards = key
        if (turn != notes.turn or who != seat or target not in range(4) or target == seat
                or origin not in DRAFT_ORIGINS or not 0 <= steps <= 16
                or min(give_cash, get_cash, give_cards, get_cards) < 0
                or max(give_cards, get_cards) > 2 or give_cash and get_cash):
            raise ValueError("Invalid pending offer note")
        from ..board import BUYABLE
        if any(sp not in BUYABLE for sp in give + get) or len(set(give + get)) != len(give + get):
            raise ValueError("Invalid pending offer properties")
    if notes.turn == -1 and (notes.sequence or rows or notes.pending or notes.final_kind or notes.revision is not None):
        raise ValueError("Uninitialized notes contain history")
    result = NoteOverlay(seat)
    for key in _FIELDS:
        setattr(result, key, getattr(notes, key))
    result._publish()
    return result


def decision_meta_from(space, first, top=6):
    """The leading legal choices of the decision with their probabilities, and the value estimate when the model has one."""
    if not first:
        return None
    probs, mask = first["probs"], first["mask"]
    order = [int(i) for i in np.argsort(-probs) if mask[int(i)]][:top]
    rows, seen = [], set()
    for index in order:
        try:
            action = space.to_action(deepcopy(first["game"]), index)
            label = describe_action(public_action(action)) if action is not None else "Build a trade offer"
        except Exception:  # noqa: BLE001 -- a step of an offer has no single action
            label = "Build a trade offer"
        if label in seen:
            continue
        seen.add(label)
        rows.append({"action": label, "probability": round(float(probs[index]), 4)})
    meta = {"probabilities": rows}
    if first.get("value") is not None:
        meta["value"] = round(float(first["value"]), 4)
    return meta


class RentcraftInferenceBot:
    def __init__(self, profile, policy, seed):
        if (profile != resolve_profile(profile.id, profile.version) or not isinstance(policy, NumpyPolicy)
                or policy.profile != profile or type(seed) is not int):
            raise ValueError("Model policy and input profile do not match")
        self.profile, self.policy = profile, policy
        network = dict(policy.architecture)
        if "hidden" in network:
            network["hidden"] = list(network["hidden"])
        self._binding = digest({"profile": profile.identities(), "architecture": network,
                                "policy": canonical_tensor_digest(policy.weights), "selection": "greedy"})
        self._notes = None

    def initialize(self, observation):
        """Bind a seat before the first move, including a nonacting seat."""
        require_model_context(observation)
        if (observation.game_id != "rentcraft" or observation.revision != 0
                or type(observation.viewer) is not int or observation.viewer not in range(4)):
            raise ValueError("A model must be bound before the game starts")
        if self._notes is not None:
            raise ValueError("Model seat is already initialized")
        self._notes = NoteOverlay(observation.viewer)

    def choose(self, observation, legal_actions, *, allow_trades=True):
        if (observation.game_id != "rentcraft" or observation.decision is None
                or observation.decision.player != observation.viewer):
            raise ValueError("A model needs its own Rentcraft decision")
        notes = deepcopy(self._notes)
        if notes is None:
            if observation.revision != 0:
                raise ValueError("A model must be bound before the game starts")
            notes = NoteOverlay(observation.viewer)
        game = DecisionGame.from_observation(observation, notes)
        decision = game.decision()
        if (decision.phase.value != observation.decision.kind
                or tuple(public_action(a) for a in decision.actions) != tuple(legal_actions)):
            raise ValueError("Decision and legal actions do not match the observed board")
        encoder, space = self.profile.encoder(), self.profile.action_space()
        first = {}
        def pick(mask):
            if not allow_trades:
                mask = without_trade_starts(space, game, mask)
            encoded = encoder.encode(game.state, observation.viewer, game)
            index = self.policy.best(encoded, mask)
            if not first:
                # The first choice of a decision is also recorded: its action probabilities and the value estimate.
                value = getattr(self.policy, "value", None)
                first.update(probs=np.asarray(self.policy.probs(encoded, mask)), mask=np.array(mask), game=deepcopy(game),
                             value=value(encoded) if callable(value) and getattr(self.policy, "value_weights", None) else None)
            return index
        action, _ = choose_index(space, game, pick)
        selected = public_action(action)
        if selected not in legal_actions and action.kind not in (ActionKind.PROPOSE_TRADE, ActionKind.COUNTER_TRADE):
            raise ValueError("Model selected an unavailable action")
        self._notes = notes
        return BotChoice(selected, "Selected by the greedy observation policy.", meta=decision_meta_from(space, first))

    def save_state(self):
        return {"version": 1, "binding": self._binding, "selection": "greedy",
                "seat": None if self._notes is None else self._notes.seat,
                "notes": None if self._notes is None else plain(_Notes(**{k: getattr(self._notes, k) for k in _FIELDS}))}

    def load_state(self, state):
        if (type(state) is not dict or set(state) != {"version", "binding", "selection", "seat", "notes"}
                or type(state["version"]) is not int or state["version"] != 1
                or state["binding"] != self._binding or state["selection"] != "greedy"):
            raise ValueError("Model checkpoint does not match the selected policy")
        seat = state["seat"]
        if seat is None:
            if state["notes"] is not None or self._notes is not None:
                raise ValueError("Invalid unbound model checkpoint")
            return
        if type(seat) is not int or seat not in range(4) or self._notes is not None and seat != self._notes.seat:
            raise ValueError("Model checkpoint belongs to another seat")
        notes = _restore_notes(seat, state["notes"])
        self._notes = notes
