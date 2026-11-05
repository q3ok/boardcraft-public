"""Dice Circuit metadata and packaged play resources."""
from importlib.resources import files
from typing import Mapping

from boardcraft.contracts import Action, Bot, BuiltinBotInfo, Encoder, GameConfig, GameEngine, GameInfo, JsonValue, PlayCapabilities
from boardcraft.settings import normalize_settings
from .actions import describe_action
from .sheet import sheet_data
from .bots import RandomBot, GreedyBot, StrategistBot

# Version 1 is the behavior shipped in the original playable release.
_BUILTINS = ((BuiltinBotInfo('random', 'Random bot', '1', '1'), RandomBot),
             (BuiltinBotInfo('greedy', 'Greedy bot', '1', '1'), GreedyBot),
             (BuiltinBotInfo('strategist', 'Strategist', '1', '1'), StrategistBot))


class DiceCircuitModule:
    game_id = 'dice_circuit'
    description: str = 'Choose dice and fill a scoring sheet, linking bonuses across its colored areas.'
    bot_descriptions: Mapping[str, str] = {
        'random': 'Chooses among legal actions using a seeded random policy.',
        'greedy': 'Chooses actions using the immediate value of scoring entries and bonuses.',
        'strategist': 'Plans bonus chains, emblems and balanced scoring while reserving dice and resources.',
    }
    rules_version = '1'
    engine_version = '1'
    observation_version = '1'
    action_version = '1'
    # Surrender is a seat operation of live tables only; simulated games and their records never contain one.
    live_seat_operations = True
    # Every viewer sees the game_started event, so a new game is at revision 1 for everyone.
    initial_revision = 1

    def info(self) -> GameInfo:
        return GameInfo(self.game_id, self.rules_version, self.engine_version,
                        'Dice Circuit', ('base',), 1, 4, tuple(item.kind for item, _ in _BUILTINS),
                        PlayCapabilities(neural_inference=True))

    def new(self, config: GameConfig, seed: int) -> GameEngine:
        from .engine import DiceCircuitEngine
        return DiceCircuitEngine.new(config, seed)

    def restore(self, snapshot: dict[str, JsonValue]) -> GameEngine:
        from .engine import DiceCircuitEngine
        return DiceCircuitEngine.restore(snapshot)

    def simulation_defaults(self):
        return {}

    def decode_result(self, data: dict, players: int):
        from .scoring import COLOURS, DiceCircuitResult
        keys = ('scores', 'area_scores', 'emblem_scores', 'placements', 'reason')
        if (not isinstance(data, dict) or set(data) != set(keys)
                or not isinstance(data['reason'], str) or not data['reason']):
            raise ValueError('Invalid Dice Circuit record result fields')
        for key in keys[:-1]:
            if type(data[key]) is not list or len(data[key]) != players:
                raise ValueError('Invalid Dice Circuit record result player count')
        if (any(type(v) is not int for key in ('scores', 'emblem_scores', 'placements') for v in data[key])
                or any(p < 1 for p in data['placements'])
                or any(type(row) is not dict or set(row) != set(COLOURS)
                       or any(type(v) is not int for v in row.values()) for row in data['area_scores'])):
            raise ValueError('Invalid Dice Circuit record result values')
        return DiceCircuitResult(tuple(data['scores']), tuple(dict(row) for row in data['area_scores']),
                          tuple(data['emblem_scores']), tuple(data['placements']), data['reason'])

    def rules_text(self) -> str:
        return files('boardcraft').joinpath('games/dice_circuit/content/rules.en.md').read_text(encoding='utf-8')

    def sheet_spec(self) -> dict[str, JsonValue]:
        return sheet_data()

    def board_spec(self) -> dict[str, JsonValue]:
        return self.sheet_spec()

    def settings_schema(self) -> dict[str, JsonValue]:
        return {'type': 'object', 'properties': {}, 'additionalProperties': False}

    def bot_settings_schema(self, kind: str) -> dict[str, JsonValue]:
        if kind not in tuple(info.kind for info, _ in _BUILTINS):
            raise ValueError('Unknown Dice Circuit bot kind')
        return self.settings_schema()

    def describe_action(self, action: Action) -> str:
        return describe_action(action)

    def describe_actions(self, actions, observation) -> list[str]:
        """Describe a legal set using only its owning player's permitted view."""
        return [describe_action(action, observation) for action in actions]

    def table_round(self, engine):
        """The round in play and the round count of this player count (for the table list)."""
        from .sheet import load_sheet
        return engine._state.round_no, load_sheet().round_counts[engine.config.players - 1]

    def replay_metrics(self, observation):
        """Public summary of one replay position: the visible score of every sheet."""
        return {'scores': [item['total'] for item in observation.data['score_summary']]}

    def encoder(self) -> Encoder:
        from .encoder import DiceCircuitEncoder
        return DiceCircuitEncoder()

    def bot(self, kind: str, seed: int, *, options=None) -> Bot:
        normalize_settings(self.bot_settings_schema(kind), {} if options is None else options)
        for info, factory in _BUILTINS:
            if info.kind == kind:
                return factory(seed)
        raise ValueError('Unknown Dice Circuit bot kind')

    def bot_info(self) -> tuple[BuiltinBotInfo, ...]:
        return tuple(info for info, _ in _BUILTINS)
