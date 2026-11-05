"""Strict public tournament inputs shared by HTTP, MCP and the creator."""

from copy import deepcopy
from typing import Annotated, Literal

from pydantic import Field, StrictInt

from .schemas import ConfigInput, Input, SeatInput

Integer = Annotated[StrictInt, Field(ge=0, le=2**53 - 1)]
Positive = Annotated[StrictInt, Field(ge=1, le=2**53 - 1)]

_STANDARD_RATING_PROFILE = {
    "id": "standard_l2_v1",
    "title": "Standard · L2 stabilization",
    "description": (
        "Fits observed placements with L2 stabilization. A conservative starting "
        "engineering profile, not an empirically calibrated optimum."
    ),
    "fit_spec": {
        "gauge": "component_sum_zero",
        "elo_origin": 1500.0,
        "ridge_lambda": 2.5,
        "prior_pseudocount": 0.0,
        "solver": "newton_backtracking",
        "max_iterations": 100,
        "max_backtracks": 30,
        "gradient_inf_tolerance": 1e-8,
        "armijo_coefficient": 1e-4,
        "backtrack_factor": 0.5,
    },
    "warnings": [
        "Stabilization pulls estimates towards the centre, especially with little evidence.",
        "Separate components and independently fitted comparisons have no shared identified order.",
        "Rotations share chance seeds within a group; match count is not an independent-sample count.",
        "Solo games provide scores, not competitive Elo.",
    ],
}


class TournamentInput(Input):
    name: str = Field(
        min_length=1, max_length=128, description="Explicit tournament name."
    )
    config: ConfigInput = Field(
        description="Exact public game, variant and player count."
    )
    rules_version: str = Field(
        min_length=1, description="Pinned canonical rules version."
    )
    engine_version: str = Field(
        min_length=1, description="Pinned canonical engine semantics."
    )
    seats: list[SeatInput] = Field(
        min_length=1,
        description="Ordered available bot choice IDs; pinned once on create. No human seats.",
    )
    seeds: list[Integer] = Field(
        min_length=1,
        description="Distinct game chance seeds, one per independent seed group.",
    )
    sampler_seeds: list[list[Integer]] = Field(
        min_length=1,
        description="One sampler seed per entrant for each seed group; rotates with its entrant.",
    )
    rotations: list[Integer] = Field(
        min_length=1,
        description="Explicit cyclic seat offsets. Choose all offsets for balanced full rotations.",
    )
    match_count: Positive = Field(
        description="Must equal seed groups times selected rotations; never silently truncated."
    )
    schedule_cap: Positive = Field(
        description="Maximum materialized scheduled matches."
    )
    max_inflight: Positive = Field(
        description="Maximum owned simultaneous match tasks."
    )
    max_record_bytes: Positive = Field(
        description="Maximum compressed and expanded record bytes per match."
    )
    max_decisions: Positive = Field(
        description="Maximum accepted decisions in a complete match record."
    )
    start: bool = Field(
        default=False,
        description=(
            "Start right away in the same request: the server owns starting, and starts a saved tournament itself "
            "after a restart if it was not started yet. False only saves it (start it later explicitly)."
        ),
    )
    lineups: list[list[Integer]] | None = Field(
        default=None,
        description=(
            "Pool tournament: the seats are the pool and each seed group seats these pool indices "
            "(one per player, distinct). Omit for a fixed line-up where every group seats all seats."
        ),
    )


class RatingFitInput(Input):
    gauge: Literal["component_sum_zero"] = Field(
        description="Zero mean log strength separately in each observed connected component."
    )
    elo_origin: float = Field(
        allow_inf_nan=False,
        description="Arbitrary finite Elo display origin per component; differences use 400 log10 odds.",
    )
    ridge_lambda: float = Field(
        ge=0,
        allow_inf_nan=False,
        description="Log-strength ridge coefficient: lambda/2 times sum beta squared. Zero disables.",
    )
    prior_pseudocount: float = Field(
        ge=0,
        allow_inf_nan=False,
        description="Symmetric pseudo-wins per direction on observed edges only. Zero disables.",
    )
    solver: Literal["newton_backtracking"] = Field(
        description="Analytic NumPy Newton solve with declared Armijo backtracking."
    )
    max_iterations: Positive = Field(
        description="Explicit maximum solver iterations; exhaustion reports failure."
    )
    max_backtracks: Positive = Field(
        description="Explicit maximum line-search trials per iteration."
    )
    gradient_inf_tolerance: float = Field(
        gt=0,
        allow_inf_nan=False,
        description="Required convergence tolerance in sum-zero solver coordinates.",
    )
    armijo_coefficient: float = Field(
        gt=0,
        lt=1,
        allow_inf_nan=False,
        description="Armijo sufficient-decrease coefficient.",
    )
    backtrack_factor: float = Field(
        gt=0,
        lt=1,
        allow_inf_nan=False,
        description="Line-search step multiplier after each rejected trial.",
    )


class RevisionInput(Input):
    revision: Integer


class StartTournamentInput(RevisionInput):
    spec_sha: str = Field(pattern="^[0-9a-f]{64}$")


class FitTournamentInput(RevisionInput):
    fit: RatingFitInput


def recommended_fit():
    """The fit the server applies to every completed line-up tournament (the recommended rating profile)."""
    return deepcopy(_STANDARD_RATING_PROFILE["fit_spec"])


def tournament_schema():
    return {
        "tournament": TournamentInput.model_json_schema(),
        "fit": RatingFitInput.model_json_schema(),
        "recommended_rating_profile": _STANDARD_RATING_PROFILE["id"],
        "rating_profiles": [deepcopy(_STANDARD_RATING_PROFILE)],
    }


def register_tournament_routes(app, service, credential):
    from fastapi import Header, Query

    @app.get("/api/tournaments/schema")
    def schema():
        return tournament_schema()

    @app.get("/api/tournaments")
    def listing():
        return service.list_tournaments()

    @app.post("/api/tournaments")
    def create(body: TournamentInput):
        return service.create_tournament(body.model_dump())

    @app.get("/api/tournaments/{id}")
    def status(id: str, matches: bool = True):
        # matches=false: the stored summary only (progress, per-entrant results and solo scores), no match rows.
        return service.get_tournament(id, matches)

    @app.get("/api/tournaments/{id}/matches")
    def match_page(id: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
        return service.tournament_matches(id, offset, limit)

    @app.post("/api/tournaments/{id}/start")
    def start(
        id: str,
        body: StartTournamentInput,
        authorization: str | None = Header(default=None),
    ):
        return service.start_tournament(
            id, credential(authorization), body.spec_sha, body.revision
        )

    @app.post("/api/tournaments/{id}/restart")
    def restart(
        id: str,
        body: StartTournamentInput,
        authorization: str | None = Header(default=None),
    ):
        return service.restart_tournament(
            id, credential(authorization), body.spec_sha, body.revision
        )

    @app.post("/api/tournaments/{id}/stop")
    def stop(
        id: str, body: RevisionInput, authorization: str | None = Header(default=None)
    ):
        return service.stop_tournament(id, credential(authorization), body.revision)

    @app.post("/api/tournaments/{id}/delete")
    def delete(
        id: str, body: RevisionInput, authorization: str | None = Header(default=None)
    ):
        return service.delete_tournament(id, credential(authorization), body.revision)

    @app.post("/api/tournaments/{id}/fit")
    def fit(
        id: str,
        body: FitTournamentInput,
        authorization: str | None = Header(default=None),
    ):
        return service.fit_tournament_ratings(
            id, credential(authorization), body.revision, body.fit.model_dump()
        )

    @app.get("/api/tournaments/{id}/ratings")
    def ratings(id: str):
        return service.get_tournament_ratings(id)

    @app.get("/api/tournaments/{id}/matches/{match_id}/replay")
    def match_replay(id: str, match_id: str, position: int = Query(0, ge=0)):
        return service.get_tournament_match_replay(id, match_id, position)
