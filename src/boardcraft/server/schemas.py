"""Strict transport input validation without game-specific defaults."""
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from typing import Annotated, Literal
from boardcraft.contracts import GameConfig


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class IdempotencyInput(Input):
    idempotency_key: str | None = Field(default=None, min_length=32, max_length=256, pattern=r'^[!-~]+$')


class ConfigInput(Input):
    game_id: str = Field(min_length=1, max_length=80)
    variant: str = Field(min_length=1, max_length=80)
    players: StrictInt = Field(ge=1)

    settings: dict = Field(default_factory=dict, exclude_if=lambda values: not values)

    def game_config(self):
        return GameConfig(self.game_id, self.variant, self.players, self.settings)


ChoiceId = Annotated[str, Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')]


class BuiltinSelection(Input):
    choice_id: ChoiceId
    options: dict


HybridId = Annotated[str, Field(min_length=8, max_length=8192, pattern=r'^hybrid:.+$')]


SeatInput = ChoiceId | HybridId | BuiltinSelection
SeatName = Annotated[str, Field(min_length=1, max_length=40)]


class CreateInput(IdempotencyInput):
    config: ConfigInput
    seats: list[SeatInput] = Field(min_length=1)
    live: dict = Field(default_factory=dict)
    visibility: Literal['invite', 'listed'] = 'invite'
    # The creator's display name for each seat (any kind), in seat order; null keeps the seat's default name.
    seat_names: list[SeatName | None] | None = Field(default=None, max_length=16)


class TableListInput(Input):
    game_id: str | None = Field(default=None, min_length=1, max_length=80)
    status: Literal['lobby', 'playing', 'completed'] | None = None
    offset: StrictInt = Field(default=0, ge=0)
    limit: StrictInt = Field(default=25, ge=1, le=100)


class JoinInput(IdempotencyInput):
    seat: StrictInt = Field(ge=0)
    # The joining player's or agent's own display name; used only when the table creator did not name the seat.
    name: SeatName | None = None


class CommandInput(IdempotencyInput):
    expected_revision: StrictInt | None = Field(default=None, ge=0)
    decision_token: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    kind: str = Field(min_length=1, max_length=128)
    params: dict
    decision_id: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator('params')
    @classmethod
    def identity_from_credential(cls, value):
        if value.keys() & {'actor', 'player', 'proposer'}:
            raise ValueError('Command identity comes from the seat credential')
        return value


class ActionInput(IdempotencyInput):
    decision_token: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    decision_id: str = Field(min_length=1, max_length=256)
    action_id: str = Field(min_length=1, max_length=2048)


class WaitInput(Input):
    mode: Literal['actionable', 'changed'] = 'actionable'
    after_change_token: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    timeout: float = Field(default=25., ge=0, le=25)
