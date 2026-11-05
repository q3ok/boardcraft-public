"""Bots use permitted observations and their own reproducible random stream."""
import importlib.util
from copy import deepcopy
import pytest
from engine_helpers import new
from boardcraft.contracts import Observation
from boardcraft.games.dice_circuit.turn_actions import action


def bots():
    assert importlib.util.find_spec('boardcraft.games.dice_circuit.bots') is not None, 'Observation-only bots are missing'
    from boardcraft.games.dice_circuit.bots import RandomBot, GreedyBot
    return RandomBot, GreedyBot


@pytest.mark.parametrize('which', [0, 1])
def test_bots_choose_legal_actions_with_reproducible_english_explanations(which):
    cls = bots()[which]
    first, second = cls(42), cls(42)
    engine = new(2, 11)
    for _ in range(30):
        d = engine.decision()
        obs, legal = engine.observe(d.player), engine.legal_actions(d.player)
        choice, repeated = first.choose(obs, legal), second.choose(obs, legal)
        assert choice == repeated and choice.action in legal
        assert choice.explanation and choice.explanation.isascii()
        engine.apply(d.player, choice.action, d.id)
    with pytest.raises(ValueError, match='legal'):
        first.choose(engine.observe(0), ())


def test_greedy_prefers_visible_points_and_resources_without_engine_access():
    greedy = bots()[1](0)
    obs = new(1).observe(0)
    data = deepcopy(dict(obs.data))
    data['sheets'][0]['orange'] = [1, 1, 1]
    data['dice']['orange']['value'] = 6
    data['dice']['purple']['value'] = 1
    obs = Observation(obs.game_id, obs.rules_version, obs.observation_version, 0, 0, obs.decision, data)
    low = action('select_die', die_id='purple', area='purple', target=0)
    high = action('select_die', die_id='orange', area='orange', target=3)
    choice = greedy.choose(obs, (low, high))
    assert choice.action == high
    assert 'score' in choice.explanation.lower()
    data['sheets'][0]['orange'] = [1, 1]
    data['dice']['orange']['value'] = 1
    data['dice']['purple']['value'] = 2
    obs = Observation(obs.game_id, obs.rules_version, obs.observation_version, 0, 0, obs.decision, data)
    reward = action('select_die', die_id='orange', area='orange', target=2)
    points = action('select_die', die_id='purple', area='purple', target=0)
    assert greedy.choose(obs, (points, reward)).action == reward
