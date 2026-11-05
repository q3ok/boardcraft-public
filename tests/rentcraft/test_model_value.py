"""A model with a value head records its value estimate with the decision; one without records none."""
import numpy as np

from boardcraft.contracts import GameConfig
from boardcraft.registry import get_game
from boardcraft.games.rentcraft.inference.bot import RentcraftInferenceBot
from boardcraft.games.rentcraft.inference.policy import NumpyPolicy, weight_shapes
from boardcraft.games.rentcraft.inference.profiles import MINI, resolve_profile


def model(with_value):
    profile = resolve_profile(MINI)
    net = {"family": "rentcraft-legacy-mlp-v1", "hidden": [2], "activation": "relu", "selection": "greedy"}
    shapes = weight_shapes(profile, net, value_head=with_value)
    arrays = {k: np.zeros(s, np.float32) for k, s in shapes.items() if not k.startswith("v.")}
    value = {k: np.full(s, 0.25, np.float32) for k, s in shapes.items() if k.startswith("v.")} if with_value else None
    return RentcraftInferenceBot(profile, NumpyPolicy(profile, net, arrays, value_weights=value), 7)


def test_value_head_is_recorded_as_decision_meta_and_absent_without_it():
    engine = get_game("rentcraft").new(GameConfig("rentcraft", "base", 2), 19)
    seat = engine.decision().player
    observation, legal = engine.observe(seat), engine.legal_actions(seat)
    with_value = model(True).choose(observation, legal)
    without = model(False).choose(observation, legal)
    assert with_value.action == without.action
    assert with_value.meta["value"] == 0.25
    assert "value" not in without.meta
    assert with_value.meta["probabilities"] == without.meta["probabilities"]
