"""One request creates and starts a tournament (the server owns starting, also after a crash), and the server keeps a
global ranking per game and table size from every completed tournament: solo by mean score, tables of 2+ by rating."""
import time

import pytest

from boardcraft.server.service import PlayService


def spec(players=2, name="ranked", seats=None, seeds=(5, 6), start=False):
    seats = seats or ["random", "greedy"][:players]
    groups = len(seeds)
    return {"name": name, "config": {"game_id": "dice_circuit", "variant": "base", "players": players},
            "rules_version": "1", "engine_version": "1", "seats": seats, "seeds": list(seeds),
            "sampler_seeds": [list(range(g * players, (g + 1) * players)) for g in range(groups)],
            "rotations": list(range(players)), "match_count": groups * players, "schedule_cap": groups * players,
            "max_inflight": 2, "max_record_bytes": 50_000_000, "max_decisions": 200_000, "start": start}


def wait(predicate, timeout=120):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.1)
    return predicate()


def completed(service, id):
    return wait(lambda: service.tournaments().status(id)["status"] == "completed")


def test_one_request_creates_and_starts_a_tournament(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 1)
    try:
        made = service.create_tournament(spec(start=True))
        assert made["status"] in ("running", "completed") and made["revision"] >= 1
        assert "start_error" not in made
        assert completed(service, made["tournament_id"])
        # Without start the tournament is only saved (the explicit start stays available).
        saved = service.create_tournament(spec(name="later"))
        assert saved["status"] == "pending"
        assert service.tournaments().status(saved["tournament_id"])["status"] == "pending"
        # A saved tournament without autostart is never started by the recovery.
        assert service.tournaments().recover_starts() == []
    finally:
        service.close_tournaments()


def test_a_crash_between_saving_and_starting_is_repaired_by_the_server(tmp_path, monkeypatch):
    service = PlayService(tmp_path, seed_factory=lambda: 2)
    try:
        controller = service.tournaments()
        monkeypatch.setattr(controller, "start_saved", lambda id: (_ for _ in ()).throw(RuntimeError("server went down")))
        made = service.create_tournament(spec(start=True))
        assert made["status"] == "pending" and made["start_error"] == "server went down"
    finally:
        service.close_tournaments()
    restarted = PlayService(tmp_path, seed_factory=lambda: 2)
    try:
        restarted.recover_tournaments()
        assert completed(restarted, made["tournament_id"])
        # Idempotent: started once; another recovery finds nothing to start.
        assert restarted.tournaments().recover_starts() == []
    finally:
        restarted.close_tournaments()


def test_the_http_create_starts_in_the_same_request(tmp_path):
    from fastapi.testclient import TestClient
    from boardcraft.server.api import create_app

    service = PlayService(tmp_path, seed_factory=lambda: 3)
    with TestClient(create_app(service, run_bots=False)) as client:
        made = client.post("/api/tournaments", json=spec(start=True)).json()
        assert made["status"] in ("running", "completed")
        assert wait(lambda: client.get(f"/api/tournaments/{made['tournament_id']}").json()["status"] == "completed")
        ko = client.post("/api/knockouts", json={"name": "ko", "config": {"game_id": "dice_circuit", "variant": "base", "players": 2},
                                                 "candidates": ["greedy", "random"], "sparring": ["random"], "games": 4,
                                                 "keep": 0.5, "seed": 1, "max_inflight": 2, "start": True}).json()
        assert ko["status"] == "running"
        assert client.get(f"/api/knockouts/{ko['knockout_id']}").json()["status"] in ("running", "completed")


def test_the_global_ranking_counts_every_completed_tournament(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 4)
    try:
        seeds = (11, 12, 13)
        greedy = service.create_tournament(spec(players=1, name="Solo: g", seats=["greedy"], seeds=seeds, start=True))
        rand = service.create_tournament(spec(players=1, name="Solo: r", seats=["random"], seeds=seeds, start=True))
        duel = service.create_tournament(spec(players=2, name="duel", start=True))
        for made in (greedy, rand, duel):
            assert completed(service, made["tournament_id"])
        # The server refreshes on completion (in the background); a read never computes.
        assert wait(lambda: len(service.global_ranking("dice_circuit", 1)["tables"]) == 1
                    and len(service.global_ranking("dice_circuit", 2)["tables"]) == 1, 30)
        solo = service.global_ranking("dice_circuit", 1)
        table = solo["tables"][0]
        assert solo["min_ranked_games"] == 30 and table["games"] == 6
        names = {r["name"]: r for r in table["rows"]}
        assert set(names) == {"Greedy bot", "Random bot"}
        assert all(r["kind"] == "bot" and r["games"] == 3 and r["ranked"] is False for r in table["rows"])
        assert [r["mean_score"] for r in table["rows"]] == sorted((r["mean_score"] for r in table["rows"]), reverse=True)
        assert table["reference_name"] == "Greedy bot"
        paired = names["Random bot"]["paired"]
        assert paired["pairs"] == 3 and paired["vs_name"] == "Greedy bot"
        assert names["Greedy bot"].get("paired") is None
        for r in table["rows"]:
            assert r["median"] is not None and r["best_score"] >= r["mean_score"] and r["ci"] >= 0
        multi = service.global_ranking("dice_circuit", 2)["tables"][0]
        assert multi["games"] == 4
        assert {r["name"] for r in multi["rows"]} == {"Greedy bot", "Random bot"}
        for r in multi["rows"]:
            assert r["elo"] is not None and 1 <= r["avg_place"] <= 2 and r["games"] == 4 and 0 <= r["win_rate"] <= 1
        assert service.global_ranking("dice_circuit", 3)["tables"] == []
        # Deleting a tournament takes its games out of the ranking.
        current = service.get_tournament(rand["tournament_id"])
        service.delete_tournament(rand["tournament_id"], rand["creator_credential"], current["revision"])
        assert wait(lambda: [r["name"] for r in service.global_ranking("dice_circuit", 1)["tables"][0]["rows"]] == ["Greedy bot"], 30)
    finally:
        service.close_tournaments()
    # Startup recovery recomputes a lost ranking; refreshing again changes nothing (idempotent).
    restarted = PlayService(tmp_path, seed_factory=lambda: 4)
    try:
        with restarted.store.transaction(True) as db:
            db.execute("DELETE FROM global_rankings")
        assert restarted.global_ranking("dice_circuit", 2)["tables"] == []
        restarted.recover_tournaments()
        assert len(restarted.global_ranking("dice_circuit", 2)["tables"]) == 1
        assert restarted.refresh_rankings() == 0
        # Names saved at creation stay when the bot list cannot be read (for example a removed model).
        restarted._bot_descriptors = lambda game_id: (_ for _ in ()).throw(ValueError("gone"))
        assert {r["name"] for r in restarted.global_ranking("dice_circuit", 2)["tables"][0]["rows"]} == {"Greedy bot", "Random bot"}
    finally:
        restarted.close_tournaments()


def test_the_http_global_ranking_route(tmp_path):
    from fastapi.testclient import TestClient
    from boardcraft.server.api import create_app

    service = PlayService(tmp_path, seed_factory=lambda: 5)
    with TestClient(create_app(service, run_bots=False)) as client:
        empty = client.get("/api/rankings/global", params={"game_id": "dice_circuit", "players": 1}).json()
        assert empty["tables"] == [] and empty["players"] == 1
        assert client.get("/api/rankings/global", params={"game_id": "nope", "players": 1}).status_code == 400
        assert client.get("/api/rankings").json()["items"] == []


def test_a_name_never_falls_back_to_an_identity_hash():
    from boardcraft.server.leaderboard import _fallback_name

    assert _fallback_name({"kind": "model", "architecture_json": '{"family":"transformer","layers":2,"dim":32}'}) == "Removed model (transformer 2x32)"
    assert _fallback_name({"kind": "builtin", "builtin_kind": "strategist"}) == "Strategist"
    with pytest.raises(ValueError):
        PlayService.global_ranking(PlayService.__new__(PlayService), "dice_circuit", 0)
