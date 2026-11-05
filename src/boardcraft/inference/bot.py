"""Policy bot restricted to the public observation and legal actions."""

import numpy as np
from boardcraft.contracts import BotChoice

# Retained only to decode historical sampled policy identities.
SAMPLING_VERSION = "numpy-choice-v1"
GREEDY_SAMPLING_VERSION = "numpy-argmax-v1"


def decision_meta(encoder, probs, mask, legal_actions, describe=None, value=None, top=6):
    """The leading legal choices with their probabilities, and the value estimate when the model has one."""
    by_index = {encoder.encode_action(action): action for action in legal_actions}
    rows = []
    for index in [int(i) for i in np.argsort(-probs, kind="stable") if mask[int(i)]][:top]:
        action = by_index.get(index)
        if action is None:
            continue
        try:
            label = describe(action) if describe else action.id
        except Exception:  # noqa: BLE001 -- an action without a description is shown by its id
            label = action.id
        rows.append({"action": label, "probability": round(float(probs[index]), 4)})
    meta = {"probabilities": rows}
    if value is not None:
        meta["value"] = round(float(value), 4)
    return meta


class InferenceBot:
    def __init__(self, encoder, policy, seed, *, describe=None):
        self.encoder, self.policy = encoder, policy
        self.describe = describe
        self.sampling_version = GREEDY_SAMPLING_VERSION

    def choose(self, observation, legal_actions):
        mask = self.encoder.mask(observation, legal_actions)
        encoded = self.encoder.encode(observation)
        logits = getattr(self.policy, "logits", None)
        scores = logits(encoded) if callable(logits) else self.policy.probs(encoded, mask)
        if scores.shape != mask.shape or not mask.any():
            raise ValueError("A matching nonempty legal mask is required")
        index = int(np.where(mask, scores, -np.inf).argmax())
        selected = next(
            (
                action
                for action in legal_actions
                if self.encoder.encode_action(action) == index
            ),
            None,
        )
        if selected is None:
            raise ValueError("Policy action is outside supplied legal actions")
        # The recorded reasoning: the policy's probabilities over the legal moves (and the value estimate, if any).
        # It never changes the move, which stays the highest scoring legal action.
        try:
            masked = np.where(mask, scores, -np.inf)
            probs = np.exp(masked - masked.max())
            probs = probs / probs.sum()
            value = getattr(self.policy, "value", None)
            meta = decision_meta(self.encoder, probs, mask, legal_actions, self.describe,
                                 value(encoded) if callable(value) and getattr(self.policy, "has_value", False) else None)
        except Exception:  # noqa: BLE001 -- reasoning is optional; the move is not
            meta = None
        return BotChoice(
            self.encoder.decode_action(index),
            "Selected by the greedy observation policy.",
            meta=meta,
        )
