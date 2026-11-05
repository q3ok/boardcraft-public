"""A Dice Circuit model records its reasoning: probabilities of the leading legal moves and, with a value head, a value."""
import numpy as np
import pytest

from boardcraft.contracts import GameConfig
from boardcraft.inference.bot import InferenceBot
from boardcraft.inference.model import NumpyPolicy
from boardcraft.registry import get_game


def policy(value=False):
    module = get_game("dice_circuit")
    encoder = module.encoder()
    size, out = encoder.encoder_spec()["flat_size"], encoder.action_spec()["size"]
    rng = np.random.default_rng(3)
    weights = {"trunk.layers.0.weight": rng.normal(0, 0.05, (8, size)).astype(np.float32),
               "trunk.layers.0.bias": np.zeros(8, np.float32),
               "pi.weight": rng.normal(0, 0.5, (out, 8)).astype(np.float32),
               "pi.bias": np.zeros(out, np.float32)}
    head = {"v.weight": np.full((1, 8), 0.25, np.float32), "v.bias": np.array([0.5], np.float32)} if value else None
    return module, encoder, NumpyPolicy(encoder, {"family": "mlp", "hidden": [8], "activation": "relu"}, weights, value_weights=head)


def test_the_model_records_probabilities_and_its_value_without_changing_the_move():
    module, encoder, plain = policy()
    _, _, valued = policy(value=True)
    engine = module.new(GameConfig("dice_circuit", "base", 1), 11)
    decision = engine.decision()
    observation, legal = engine.observe(decision.player), tuple(engine.legal_actions(decision.player))
    first = InferenceBot(encoder, plain, 0, describe=module.describe_action).choose(observation, legal)
    second = InferenceBot(encoder, valued, 0, describe=module.describe_action).choose(observation, legal)
    assert first.action == second.action
    rows = first.meta["probabilities"]
    assert 1 <= len(rows) <= 6 and rows[0]["action"] == module.describe_action(first.action)
    assert all(a["probability"] >= b["probability"] for a, b in zip(rows, rows[1:]))
    assert "value" not in first.meta
    expected = valued.value(encoder.encode(observation))
    assert second.meta["value"] == pytest.approx(round(expected, 4))
    assert plain.value(encoder.encode(observation)) is None


def test_a_value_head_of_the_wrong_shape_is_refused():
    module = get_game("dice_circuit")
    encoder = module.encoder()
    size, out = encoder.encoder_spec()["flat_size"], encoder.action_spec()["size"]
    weights = {"pi.weight": np.zeros((out, size), np.float32), "pi.bias": np.zeros(out, np.float32)}
    with pytest.raises(ValueError, match="value head"):
        NumpyPolicy(encoder, {"family": "mlp", "hidden": [], "activation": "relu"}, weights,
                    value_weights={"v.weight": np.zeros((1, 3), np.float32), "v.bias": np.zeros(1, np.float32)})
