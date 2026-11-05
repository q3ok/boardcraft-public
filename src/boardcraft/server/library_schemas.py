"""Runtime-free strict library request models shared by HTTP and MCP."""
from typing import Literal

from pydantic import Field, StrictInt
from .schemas import Input
from .tournaments import RatingFitInput


class PageInput(Input):
    offset: StrictInt = Field(default=0, ge=0)
    limit: StrictInt = Field(default=25, ge=1, le=100)


class RankingListInput(PageInput):
    game_id: str | None = Field(default=None, min_length=1, max_length=80)
    rules_version: str | None = Field(default=None, min_length=1, max_length=128)
    variant: str | None = Field(default=None, min_length=1, max_length=128)
    players: StrictInt | None = Field(default=None, ge=1)
    settings_digest: str | None = Field(default=None, pattern='^([0-9a-f]{64})?$')
    tournament_id: str | None = Field(default=None, pattern='^[0-9a-f]{32}$')


class ReplayListInput(PageInput):
    game_id: str | None = Field(default=None, min_length=1, max_length=80)
    source: Literal['table', 'tournament'] | None = None
    tournament_id: str | None = Field(default=None, pattern='^[0-9a-f]{32}$')
    # The search of the replay list, done by the server: a part of a player's name (with ``won``: only the games
    # that player won) and a part of the tournament name.
    q: str | None = Field(default=None, max_length=80)
    won: bool = False
    tournament: str | None = Field(default=None, max_length=120)


class ReplayRef(Input):
    source: Literal['table', 'tournament']
    table_id: str | None = Field(default=None, pattern='^[0-9a-f]{32}$')
    tournament_id: str | None = Field(default=None, pattern='^[0-9a-f]{32}$')
    match_id: str | None = Field(default=None, min_length=1, max_length=128)


class DeleteReplaysInput(Input):
    replays: list[ReplayRef] = Field(min_length=1, max_length=500)


class RankingSource(Input):
    tournament_id: str = Field(pattern='^[0-9a-f]{32}$')
    spec_sha: str = Field(pattern='^[0-9a-f]{64}$')
    revision: StrictInt = Field(ge=0)
    credential: str = Field(min_length=1, max_length=256, repr=False)


class FitRankingsInput(Input):
    sources: list[RankingSource] = Field(min_length=1, max_length=64)
    fit: RatingFitInput


class RankingInput(Input):
    comparison_id: str = Field(pattern='^(c\\.[0-9a-f]{32}|t\\.[0-9a-f]{32}\\.[0-9a-f]{64}\\.[0-9a-f]{64})$')
    result_sha: str = Field(pattern='^[0-9a-f]{64}$')


