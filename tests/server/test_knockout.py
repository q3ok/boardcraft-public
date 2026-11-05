"""Knockout tournaments (successive halving over line-up tournaments) and pool line-ups."""
import time

import pytest

from boardcraft.server.knockout import halving_plan, standings
from boardcraft.server.service import PlayService
from boardcraft.server.views import AccessDenied


def test_halving_plan_matches_the_shared_design():
    assert halving_plan(4, 400, 0.5, 1) == [
        {"round": 1, "candidates": 4, "survivors": 2, "games": 200},
        {"round": 2, "candidates": 2, "survivors": 1, "games": 200},
    ]
    assert [r["candidates"] for r in halving_plan(5, 90, 0.66, 2)] == [5, 4, 3]
    rows = [{"candidate": 0, "games": 10, "wins": 5, "place": 15}, {"candidate": 1, "games": 10, "wins": 5, "place": 12},
            {"candidate": 2, "games": 10, "wins": 2, "place": 11}]
    assert [r["candidate"] for r in standings(rows, "wins")] == [1, 0, 2]
    assert [r["candidate"] for r in standings(rows, "place")] == [2, 1, 0]


def knockout(seed=7, games=24):
    return {"name": "pick", "config": {"game_id": "dice_circuit", "variant": "base", "players": 2},
            "candidates": ["random", "greedy", "strategist"], "sparring": ["random"], "games": games, "keep": 0.5,
            "min_candidates": 1, "metric": "wins", "seed": seed, "max_inflight": 4}


def test_a_knockout_plays_rounds_and_the_weaker_candidates_drop_out(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 3)
    try:
        made = service.knockouts().create(knockout())
        with pytest.raises(AccessDenied):
            service.knockouts().start(made["knockout_id"], "not-the-creator", 0)
        state = service.knockouts().start(made["knockout_id"], made["creator_credential"], 0)
        assert [p["candidates"] for p in state["plan"]] == [3, 2]
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            state = service.knockouts().status(made["knockout_id"])
            if state["status"] not in ("running", "pending"):
                break
            time.sleep(0.3)
        assert state["status"] == "completed", state.get("error")
        first, second = state["rounds"]
        assert first["finished"] and len(first["survivors"]) == 2 and len(first["table"]) == 3
        assert second["candidates"] == 2
        assert state["winner"] in first["survivors"] and state["top"] == [state["winner"]]
        # Results of the earlier round count: the finalists have more games than the eliminated candidate.
        out = next(r for r in first["table"] if r["candidate"] not in first["survivors"])
        assert all(r["games"] > out["games"] for r in second["table"])
        assert state["labels"]["candidates"] == ["Random bot", "Greedy bot", "Strategist"]
        assert "secrets" not in state and "credential_sha" not in state
        assert service.knockouts().delete(made["knockout_id"], made["creator_credential"], state["revision"])["status"] == "deleted"
        assert service.knockouts().list() == []
    finally:
        service.close_tournaments()


def test_a_knockout_rejects_an_impossible_plan(tmp_path):
    service = PlayService(tmp_path)
    with pytest.raises(ValueError, match="sparring"):
        service.knockouts().create({**knockout(), "sparring": ["random", "greedy"]})
    with pytest.raises(ValueError, match="Too few games"):
        service.knockouts().create(knockout(games=3))


def test_a_pool_tournament_seats_the_listed_line_ups(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 2)
    try:
        body = {"name": "pool", "config": {"game_id": "dice_circuit", "variant": "base", "players": 2},
                "rules_version": "1", "engine_version": "1", "seats": ["random", "greedy", "strategist"],
                "seeds": [11, 12, 13], "sampler_seeds": [[0, 1], [2, 3], [4, 5]], "lineups": [[0, 1], [0, 2], [1, 2]],
                "rotations": [0, 1], "match_count": 6, "schedule_cap": 6, "max_inflight": 2,
                "max_record_bytes": 50_000_000, "max_decisions": 200_000}
        with pytest.raises(ValueError):
            service.create_tournament({**body, "lineups": [[0, 0], [0, 2], [1, 2]]})
        made = service.create_tournament(body)
        service.start_tournament(made["tournament_id"], made["creator_credential"], made["spec_sha"], made["revision"])
        deadline = time.monotonic() + 120
        while service.get_tournament(made["tournament_id"])["status"] != "completed" and time.monotonic() < deadline:
            time.sleep(0.2)
        t = service.get_tournament(made["tournament_id"])
        assert t["status"] == "completed" and t["spec"]["lineups"] == [[0, 1], [0, 2], [1, 2]]
        assert [e["choice_id"] for e in t["entrants"]] == ["random", "greedy", "strategist"]
        greedy = t["spec"]["references"][1]["identity"]
        # Line-up [0, 2] never seats the greedy bot.
        assert all(greedy not in m["result"]["identities"] for m in t["matches"] if m["ordinal"] in (2, 3))
        assert all(greedy in m["result"]["identities"] for m in t["matches"] if m["ordinal"] in (0, 1, 4, 5))
    finally:
        service.close_tournaments()


def test_knockout_routes_create_read_and_guard_the_creator(tmp_path):
    from fastapi.testclient import TestClient
    from boardcraft.server.api import create_app
    with TestClient(create_app(PlayService(tmp_path), run_bots=False)) as client:
        made = client.post("/api/knockouts", json=knockout()).json()
        assert made["status"] == "pending" and len(made["knockout_id"]) == 32
        assert [k["knockout_id"] for k in client.get("/api/knockouts").json()] == [made["knockout_id"]]
        assert client.post(f"/api/knockouts/{made['knockout_id']}/delete", json={"revision": 0}).status_code == 403
        assert client.get("/api/knockouts/" + "0" * 32).status_code == 400
        deleted = client.post(f"/api/knockouts/{made['knockout_id']}/delete", json={"revision": 0},
                              headers={"Authorization": f"Bearer {made['creator_credential']}"})
        assert deleted.json()["status"] == "deleted"


def test_a_knockout_goes_on_after_the_server_restarts(tmp_path):
    first = PlayService(tmp_path, seed_factory=lambda: 4)
    made = first.knockouts().create({**knockout(), "games": 60})
    first.knockouts().start(made["knockout_id"], made["creator_credential"], 0)
    time.sleep(1.0)
    first.close_tournaments()
    second = PlayService(tmp_path, seed_factory=lambda: 4)
    try:
        deadline = time.monotonic() + 240
        state = second.knockouts().status(made["knockout_id"])
        while state["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.3)
            state = second.knockouts().status(made["knockout_id"])
        assert state["status"] == "completed", state.get("error")
        assert state["winner"] is not None
    finally:
        second.close_tournaments()
