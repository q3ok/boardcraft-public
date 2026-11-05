"""Shared tournament transports and inert creation with injected work ports."""

from boardcraft.server.api import create_app
from boardcraft.server.service import PlayService
from fastapi.testclient import TestClient


def body():
    return {
        "name": "Fixture",
        "config": {"game_id": "dice_circuit", "variant": "base", "players": 1},
        "rules_version": "1",
        "engine_version": "1",
        "seats": ["random"],
        "seeds": [11],
        "sampler_seeds": [[21]],
        "rotations": [0],
        "match_count": 1,
        "schedule_cap": 10,
        "max_inflight": 1,
        "max_record_bytes": 1000000,
        "max_decisions": 1000,
    }


def test_create_schema_status_are_inert_and_credential_required(tmp_path):
    service = PlayService(tmp_path)

    def forbidden(*a):
        raise AssertionError("Read/create must not resolve bots")

    service._provider_resolve = forbidden
    with TestClient(create_app(service, run_bots=False)) as client:
        schema = client.get("/api/tournaments/schema")
        assert schema.status_code == 200
        made = client.post("/api/tournaments", json=body())
        assert made.status_code == 200, made.text
        made = made.json()
        id = made["tournament_id"]
        assert client.get("/api/tournaments/" + id).json()["status"] == "pending"
        assert client.get("/api/tournaments").json()[0]["tournament_id"] == id
        assert (
            client.post(
                "/api/tournaments/" + id + "/start",
                json={"spec_sha": made["spec_sha"], "revision": 0},
            ).status_code
            == 403
        )
        headers = {"Authorization": "Bearer " + made["creator_credential"]}
        bad = {**body(), "seeds": [True]}
        assert client.post("/api/tournaments", json=bad).status_code == 422
        assert (
            client.post(
                "/api/tournaments/" + id + "/delete",
                headers=headers,
                json={"revision": 0},
            ).json()["status"]
            == "deleted"
        )


def test_service_rating_methods_exist_and_legacy_store_default(tmp_path):
    service = PlayService(tmp_path)
    assert (
        callable(service.fit_tournament_ratings)
        and callable(service.get_tournament_ratings)
        and callable(service.get_tournament_match_replay)
    )
    made = service.create_game(
        __import__("boardcraft.contracts", fromlist=["GameConfig"]).GameConfig(
            "dice_circuit", "base", 1
        ),
        ["human"],
    )
    with service.store.transaction(True) as db:
        id = made["table_id"]
        for table in ("tables", "bootstrap", "steps", "journals"):
            db.execute("DELETE FROM " + table + " WHERE table_id=?", (id,))
    with service.store.transaction() as db:
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 0


def test_every_public_choice_has_schema_help_and_json_roundtrip():
    import json

    from boardcraft.server.tournaments import RatingFitInput, TournamentInput

    fits = {
        "gauge": "component_sum_zero",
        "elo_origin": 0.0,
        "ridge_lambda": 0.0,
        "prior_pseudocount": 0.0,
        "solver": "newton_backtracking",
        "max_iterations": 80,
        "max_backtracks": 30,
        "gradient_inf_tolerance": 1e-10,
        "armijo_coefficient": 0.01,
        "backtrack_factor": 0.5,
    }
    # A pool tournament adds its line-ups; a fixed line-up leaves them out (None). start: saved and started at once.
    for model, payload in ((TournamentInput, {**body(), "lineups": None, "start": False}), (TournamentInput, {**body(), "lineups": [[0]], "start": True}), (RatingFitInput, fits)):
        assert set(payload) == set(model.model_fields)
        assert all(field.description for field in model.model_fields.values())
        for parsed in (json.loads(json.dumps(payload)),):
            assert model.model_validate(parsed).model_dump() == payload
