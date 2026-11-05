"""Discoverable rating controls remain explicit and reproducible."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from mcp.client import Client
from pydantic import ValidationError

from boardcraft.mcp.server import create_server
from boardcraft.play.ranking import RatingFitSpec, RatingKey, fit_ratings
from boardcraft.server.api import create_app
from boardcraft.server.service import PlayService
from boardcraft.server.tournaments import (
    RatingFitInput,
    TournamentInput,
    tournament_schema,
)


STANDARD_FIT = {
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
}


def test_schema_advertises_one_complete_explained_profile_without_input_defaults():
    schema = tournament_schema()
    assert schema["tournament"] == TournamentInput.model_json_schema()
    assert schema["fit"] == RatingFitInput.model_json_schema()
    assert schema.get("recommended_rating_profile") == "standard_l2_v1"
    assert schema["rating_profiles"] == [{
        "id": "standard_l2_v1",
        "title": "Standard · L2 stabilization",
        "description": (
            "Fits observed placements with L2 stabilization. A conservative starting "
            "engineering profile, not an empirically calibrated optimum."
        ),
        "fit_spec": STANDARD_FIT,
        "warnings": [
            "Stabilization pulls estimates towards the centre, especially with little evidence.",
            "Separate components and independently fitted comparisons have no shared identified order.",
            "Rotations share chance seeds within a group; match count is not an independent-sample count.",
            "Solo games provide scores, not competitive Elo.",
        ],
    }]
    assert set(schema["fit"]["required"]) == set(STANDARD_FIT)
    assert all("default" not in field for field in schema["fit"]["properties"].values())


def test_discovered_profile_survives_validation_and_real_fit_with_custom_controls_distinct():
    profiles = tournament_schema().get("rating_profiles", [])
    assert len(profiles) == 1
    advertised = profiles[0]["fit_spec"]
    key = RatingKey("dice_circuit", "1", "base", 2)
    rows = [{
        "key": key.data(),
        "engine_version": "1",
        "identities": ["first", "second"],
        "placements": [1, 2],
        "scores": [1, 0],
    }]
    standard = RatingFitInput.model_validate(advertised).model_dump()
    result = fit_ratings(rows, key, fit_spec=RatingFitSpec(**standard))
    assert result["fit_spec"] == STANDARD_FIT
    assert result["components"][0]["status"] == "converged"
    assert all(row["elo"] is not None for row in result["ratings"])
    custom = RatingFitInput.model_validate({**advertised, "ridge_lambda": 3.0}).model_dump()
    custom_result = fit_ratings(rows, key, fit_spec=RatingFitSpec(**custom))
    assert custom_result["fit_spec"] == {**STANDARD_FIT, "ridge_lambda": 3.0}
    assert custom_result["fit_spec"] != advertised
    assert custom_result["ratings"] != result["ratings"]


@pytest.mark.parametrize("missing", STANDARD_FIT)
def test_discovered_profile_does_not_make_any_fit_control_optional(missing):
    profiles = tournament_schema().get("rating_profiles", [])
    assert len(profiles) == 1
    incomplete = dict(profiles[0]["fit_spec"])
    del incomplete[missing]
    with pytest.raises(ValidationError) as error:
        RatingFitInput.model_validate(incomplete)
    assert any(item["loc"] == (missing,) and item["type"] == "missing"
               for item in error.value.errors())


def test_editing_discovered_controls_cannot_change_future_profile_responses():
    profiles = tournament_schema().get("rating_profiles", [])
    assert len(profiles) == 1
    profiles[0]["fit_spec"]["ridge_lambda"] = 99.0
    profiles[0]["warnings"].clear()
    profiles[0]["title"] = "Custom"
    fresh = tournament_schema()["rating_profiles"][0]
    assert fresh["fit_spec"] == STANDARD_FIT
    assert fresh["title"] == "Standard · L2 stabilization"
    assert len(fresh["warnings"]) == 4


def test_http_and_mcp_expose_same_profile_without_creating_tournaments(tmp_path):
    service = PlayService(tmp_path)
    with TestClient(create_app(service, run_bots=False)) as client:
        response = client.get("/api/tournaments/schema")
        assert response.status_code == 200
        schema = response.json()
        assert schema.get("recommended_rating_profile") == "standard_l2_v1"
        assert schema["rating_profiles"][0]["fit_spec"] == STANDARD_FIT
        assert client.get("/api/tournaments").json() == []

    async def read_schema():
        async with Client(create_server(service)) as client:
            response = await client.call_tool("tournament_schema", {})
            assert not response.is_error
            assert json.loads(response.content[0].text) == schema

    asyncio.run(read_schema())
