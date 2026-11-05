"""Common game interfaces and detached public value envelopes."""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Mapping, Protocol, TypeAlias, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    import numpy as np

JsonValue: TypeAlias = "None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]"


def _copy_json(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError('JSON numbers must be finite')
        return value
    if isinstance(value, list):
        return [_copy_json(item) for item in value]
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError('JSON object keys must be strings')
        return {key: _copy_json(item) for key, item in value.items()}
    raise TypeError('Payload must contain only JSON values')


@dataclass(frozen=True)
class GameConfig:
    game_id: str
    variant: str
    players: int
    settings: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.players) is not int or self.players < 1:
            raise ValueError('Player count must be a positive integer')
        if not isinstance(self.settings, Mapping):
            raise TypeError('Settings must be a JSON object')
        object.__setattr__(self, 'settings', _copy_json(self.settings))


def config_data(config: GameConfig) -> dict[str, JsonValue]:
    """Detach configuration data while retaining the original empty-settings format."""
    data = dict(game_id=config.game_id, variant=config.variant, players=config.players)
    if config.settings:
        data['settings'] = _copy_json(config.settings)
    return data


@dataclass(frozen=True)
class Decision:
    id: str
    player: int
    kind: str


@dataclass(frozen=True)
class Action:
    id: str
    kind: str
    params: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, 'params', _copy_json(self.params))


@dataclass(frozen=True)
class Event:
    sequence: int
    kind: str
    payload: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, 'payload', _copy_json(self.payload))


@dataclass(frozen=True)
class Observation:
    game_id: str
    rules_version: str
    observation_version: str
    viewer: int | None
    revision: int
    decision: Decision | None
    data: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, 'data', _copy_json(self.data))

    @classmethod
    def owned(cls, game_id, rules_version, observation_version, viewer, revision, decision, data) -> Observation:
        """For an engine's own read-only use: data is neither copied nor checked."""
        observation = object.__new__(cls)
        for name, value in (('game_id', game_id), ('rules_version', rules_version),
                            ('observation_version', observation_version), ('viewer', viewer),
                            ('revision', revision), ('decision', decision), ('data', data)):
            object.__setattr__(observation, name, value)
        return observation


@runtime_checkable
class GameOutcome(Protocol):
    @property
    def scores(self) -> tuple[int, ...]: ...
    @property
    def placements(self) -> tuple[int, ...]: ...
    @property
    def reason(self) -> str: ...


@dataclass(frozen=True)
class GameResult:
    scores: tuple[int, ...]
    area_scores: tuple[tuple[int, ...], ...]
    emblem_scores: tuple[int, ...]
    placements: tuple[int, ...]
    reason: str


@dataclass(frozen=True)
class PlayCapabilities:
    neural_inference: bool = False
    parameterized_actions: bool = False
    seat_operations: bool = False
    stateful_bots: bool = False

    def __post_init__(self) -> None:
        if any(type(value) is not bool for value in (
            self.neural_inference, self.parameterized_actions,
            self.seat_operations, self.stateful_bots,
        )):
            raise ValueError('Play capabilities must be booleans')


@dataclass(frozen=True)
class GameInfo:
    game_id: str
    rules_version: str
    engine_version: str
    name: str
    variants: tuple[str, ...]
    min_players: int
    max_players: int
    bot_kinds: tuple[str, ...]
    capabilities: PlayCapabilities = field(default_factory=PlayCapabilities)


def decision_meta(value):
    """Validate detached, optional annotations; callers control their visibility."""
    if value is None:
        return None
    if not isinstance(value, Mapping) or not set(value) <= {"probabilities", "value", "reason"}:
        raise ValueError("Invalid decision metadata fields")
    value = _copy_json(value)
    if "value" in value and (type(value["value"]) not in (int, float)):
        raise ValueError("Invalid decision value")
    if "reason" in value and not isinstance(value["reason"], str):
        raise ValueError("Invalid decision reason")
    if "probabilities" in value:
        rows = value["probabilities"]
        if (type(rows) is not list or len(rows) > 1024
                or any(type(row) is not dict or set(row) != {"action", "probability"}
                       or not isinstance(row["action"], str) or not row["action"]
                       or type(row["probability"]) not in (int, float)
                       or not 0 <= row["probability"] <= 1 for row in rows)):
            raise ValueError("Invalid decision probabilities")
    return value or None


@dataclass(frozen=True)
class BotChoice:
    action: Action
    explanation: str
    meta: Mapping[str, JsonValue] | None = field(default=None, kw_only=True)

    def __post_init__(self):
        object.__setattr__(self, "meta", decision_meta(self.meta))


class Bot(Protocol):
    def choose(self, observation: Observation, legal_actions: tuple[Action, ...]) -> BotChoice: ...


@dataclass(frozen=True)
class BotCompatibility:
    game_id: str
    rules_version: str
    observation_version: str
    encoder_id: str
    encoder_version: str
    encoder_layout_hash: str
    action_id: str
    action_version: str
    action_layout_hash: str
    input_size: int
    output_size: int


@dataclass(frozen=True)
class RuleCompatibility:
    game_id: str
    rules_version: str
    observation_version: str
    action_version: str


@dataclass(frozen=True)
class BotReference:
    """Immutable behavior identity; aliases and display names are separate."""
    kind: str
    identity: str
    compatibility: BotCompatibility | RuleCompatibility
    policy_sha256: str | None = None
    builtin_kind: str | None = None
    behavior_version: str | None = None
    inference_format: int | None = None
    architecture_json: str | None = None
    sampling_version: str | None = None


@dataclass(frozen=True)
class RuleBotReference(BotReference):
    """Rule behavior with canonical settings and no tensor compatibility."""
    compatibility: RuleCompatibility
    options_json: str = '{}'


@dataclass(frozen=True)
class BotDescriptor:
    choice_id: str
    display_name: str
    kind: str
    reference: BotReference
    description: str | None = None


@dataclass(frozen=True)
class BuiltinBotInfo:
    kind: str
    display_name: str
    behavior_version: str
    legacy_behavior_version: str | None = None


class BotCatalog(Protocol):
    def list_bots(self, game_id: str) -> tuple[BotDescriptor, ...]: ...
    def pin(self, game_id: str, choice_id: str) -> BotReference: ...
    def pin_builtin(self, game_id: str, choice_id: str, options: dict) -> BotReference: ...
    def resolve(self, reference: BotReference, seed: int) -> Bot: ...


class Encoder(Protocol):
    def encode(self, observation: Observation) -> np.ndarray: ...
    def mask(self, observation: Observation, legal_actions: tuple[Action, ...]) -> np.ndarray: ...
    def encode_action(self, action: Action) -> int: ...
    def decode_action(self, index: int) -> Action: ...
    def encoder_spec(self) -> dict[str, JsonValue]: ...
    def action_spec(self) -> dict[str, JsonValue]: ...


class IllegalAction(ValueError):
    """An action is not legal in the current decision."""


class StaleDecision(IllegalAction):
    """The supplied decision ID is no longer current."""


class WrongPlayer(IllegalAction):
    """The player does not own an eligible decision."""


class GameEngine(Protocol):
    @property
    def config(self) -> GameConfig: ...
    @classmethod
    def new(cls, config: GameConfig, seed: int) -> GameEngine: ...
    def initial_events(self) -> tuple[Event, ...]: ...
    def project_events(self, events: tuple[Event, ...], viewer: int | None) -> tuple[Event, ...]: ...
    def decision(self) -> Decision | None: ...
    def observe(self, player: int | None) -> Observation: ...
    def legal_actions(self, player: int) -> tuple[Action, ...]: ...
    def apply(self, player: int, action: Action, decision_id: str) -> tuple[Event, ...]: ...
    def command_specs(self, viewer: int | None) -> tuple[dict, ...]: ...
    def apply_seat_operation(self, actor: int, action: Action, expected_revision: int) -> tuple[Event, ...]: ...
    def is_over(self) -> bool: ...
    def result(self) -> GameOutcome | None: ...
    def snapshot(self) -> dict[str, JsonValue]: ...
    def clone(self) -> GameEngine: ...
    def fork(self, seed: int) -> GameEngine:
        """Independently copy the present position, changing only future chance."""
        ...


class GameModule(Protocol):
    game_id: str
    rules_version: str
    engine_version: str
    observation_version: str
    action_version: str

    def simulation_defaults(self) -> dict[str, JsonValue]: ...
    def decode_result(self, data: dict, players: int) -> GameOutcome: ...
    def info(self) -> GameInfo: ...

    def new(self, config: GameConfig, seed: int) -> GameEngine: ...
    def restore(self, snapshot: dict[str, JsonValue]) -> GameEngine: ...
    def rules_text(self) -> str: ...
    def sheet_spec(self) -> dict[str, JsonValue]: ...
    def board_spec(self) -> dict[str, JsonValue]: ...
    def settings_schema(self) -> dict[str, JsonValue]: ...
    def bot_settings_schema(self, kind: str) -> dict[str, JsonValue]: ...
    def describe_action(self, action: Action) -> str: ...
    def encoder(self) -> Encoder | None: ...
    def bot(self, kind: str, seed: int, *, options: Mapping | None = None) -> Bot: ...
    def bot_info(self) -> tuple[BuiltinBotInfo, ...]: ...
