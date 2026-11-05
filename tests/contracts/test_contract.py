"""Public envelopes remain detached and modules are explicitly registered."""
from dataclasses import FrozenInstanceError
import importlib.util
import subprocess
import sys

import pytest


def contracts():
    assert importlib.util.find_spec('boardcraft.contracts') is not None, 'Common contracts are missing'
    from boardcraft import contracts as api
    return api


def test_envelopes_copy_nested_json_and_are_frozen():
    api = contracts()
    source = {'sheets': [{'marks': [2, 4]}]}
    observation = api.Observation('dice_circuit', '1', '1', 0, 2, api.Decision('p0:1', 0, 'select_die'), source)
    source['sheets'][0]['marks'].append(6)
    assert observation.data == {'sheets': [{'marks': [2, 4]}]}
    observation.data['sheets'][0]['marks'].append(9)
    assert source['sheets'][0]['marks'] == [2, 4, 6]
    with pytest.raises(FrozenInstanceError):
        observation.revision = 3
    params = {'target': [0, 1]}
    action = api.Action('yellow:0:1', 'select_die', params)
    event = api.Event(1, 'entry', params)
    params['target'].append(2)
    assert action.params['target'] == [0, 1]
    assert event.payload['target'] == [0, 1]
    assert not hasattr(observation, 'seed')
    assert not hasattr(api.GameConfig('dice_circuit', 'base', 2), 'seed')


def test_contract_rejects_non_json_payloads():
    api = contracts()
    for invalid in ({'bad': object()}, {'bad': float('nan')}, {1: 'value'}):
        with pytest.raises((TypeError, ValueError)):
            api.Event(1, 'entry', invalid)


@pytest.mark.parametrize('players', (5, 12))
def test_common_game_config_accepts_larger_game_player_counts(players):
    api = contracts()
    config = api.GameConfig('another_game', 'base', players)
    assert config.players == players


def test_game_config_requires_positive_integer_seat_count():
    api = contracts()
    for players in (0, -1, True, 2.0):
        with pytest.raises(ValueError):
            api.GameConfig('dice_circuit', 'base', players)


def test_config_settings_are_detached_and_empty_settings_keep_legacy_shape():
    api = contracts()
    source = {'nested': {'levels': [1, 2]}}
    config = api.GameConfig('another_game', 'base', 2, source)
    source['nested']['levels'].append(3)
    assert config.settings == {'nested': {'levels': [1, 2]}}
    exported = api.config_data(config)
    exported['settings']['nested']['levels'].append(4)
    assert config.settings == {'nested': {'levels': [1, 2]}}
    assert api.config_data(api.GameConfig('dice_circuit', 'base', 2)) == {
        'game_id': 'dice_circuit', 'variant': 'base', 'players': 2,
    }
    for invalid in ({'x': float('nan')}, {'x': object()}, {1: 'value'}, [], None):
        with pytest.raises((TypeError, ValueError)):
            api.GameConfig('another_game', 'base', 2, invalid)


def test_registry_resources_and_generic_module_registration():
    contracts()
    from boardcraft.registry import GameRegistry, get_game, list_games
    module = get_game('dice_circuit')
    assert 'No legal entry' in module.rules_text()
    sheet = module.sheet_spec()
    sheet['yellow']['columnPoints'][0] = -1
    assert module.sheet_spec()['yellow']['columnPoints'] == [10, 14, 16, 20]
    assert [info.game_id for info in list_games()] == ['dice_circuit', 'rentcraft']
    assert list_games()[0].rules_version == '1'
    assert module.engine_version == '1'
    engine = module.new(contracts().GameConfig('dice_circuit', 'base', 1), 0)
    assert engine.config == contracts().GameConfig('dice_circuit', 'base', 1)
    assert engine.decision() is not None
    restored = module.restore(engine.snapshot())
    assert restored.config == engine.config
    assert restored.snapshot() == engine.snapshot()

    class SeparateModule:
        game_id = 'separate'
        rules_version = '2'
        engine_version = '3'
        def info(self):
            return contracts().GameInfo(self.game_id, self.rules_version, self.engine_version,
                                        'Separate', ('base',), 1, 12, ())
        def new(self, config, seed):
            return ('new', config, seed)
        def restore(self, snapshot):
            return ('restored', snapshot)
        def rules_text(self):
            return 'Separate rules'
        def sheet_spec(self):
            return {'cells': 2}
        def describe_action(self, action):
            return 'Separate choice'

    separate = SeparateModule()
    registry = GameRegistry((separate,))
    assert registry.get_game('separate') is separate
    assert registry.list_games()[0].engine_version == '3'
    assert registry.get_game('separate').new('config', 8) == ('new', 'config', 8)
    with pytest.raises(KeyError):
        registry.get_game('missing')
    with pytest.raises(ValueError):
        GameRegistry((separate, separate))


def test_registry_import_does_not_load_engine_or_optional_services():
    contracts()
    result = subprocess.run([sys.executable, '-I', '-B', '-c',
        "import sys; import boardcraft.registry; assert not ({'fastapi', 'uvicorn', 'mcp', 'torch', 'boardcraft.games.dice_circuit.engine', 'boardcraft.games.rentcraft.engine', 'boardcraft.games.rentcraft.bots.adapter'} & set(sys.modules))"],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_action_descriptions_use_semantic_parameters():
    api = contracts()
    from boardcraft.registry import get_game
    describe = get_game('dice_circuit').describe_action
    assert describe(api.Action('x', 'select_die', {'die_id': 'white', 'area': 'yellow', 'target': [0, 1]})) == 'Use white die in yellow at row 1, column 2 (1-based; target indices are 0-based).'
    assert describe(api.Action('r', 'reroll', {})) == 'Reroll all available dice.'
    assert describe(api.Action('b', 'choose_round_bonus', {'bonus': 'black6'})) == 'Choose the black 6 bonus.'
    with pytest.raises(ValueError):
        describe(api.Action('invalid', 'unknown', {}))


def test_review_dice_implements_shared_command_contract():
    from boardcraft.contracts import Action, GameConfig, GameEngine, IllegalAction
    from boardcraft.registry import get_game

    module = get_game("dice_circuit")
    game: GameEngine = module.new(GameConfig("dice_circuit", "base", 2), 711)
    for engine in (game, module.restore(game.snapshot())):
        assert engine.command_specs(None) == ()
        for viewer in (0, 1):
            assert [spec["kind"] for spec in engine.command_specs(viewer)] == ["resign"]
        before = engine.snapshot()
        with pytest.raises(IllegalAction):
            engine.apply_seat_operation(0, Action("request_trade", "request_trade", {}), engine.observe(0).revision)
        with pytest.raises(IllegalAction):
            engine.apply_seat_operation(0, Action("resign", "resign", {}), engine.observe(0).revision + 1)
        assert engine.snapshot() == before
