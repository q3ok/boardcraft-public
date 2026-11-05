"""The server keeps the rankings: a completed line-up tournament is fitted once with the recommended profile,
without any client or credential, and the startup recovery fits what a crash left unfitted."""
import time

from boardcraft.server.service import PlayService
from boardcraft.server.tournaments import recommended_fit


def spec(players=2, name="ranked"):
    seats = ["random", "greedy"][:players]
    return {"name": name, "config": {"game_id": "dice_circuit", "variant": "base", "players": players},
            "rules_version": "1", "engine_version": "1", "seats": seats, "seeds": [5, 6],
            "sampler_seeds": [list(range(players)), list(range(players, 2 * players))],
            "rotations": list(range(players)), "match_count": 2 * players, "schedule_cap": 2 * players,
            "max_inflight": 2, "max_record_bytes": 50_000_000, "max_decisions": 200_000}


def finish(service, body):
    made = service.create_tournament(body)
    service.start_tournament(made["tournament_id"], made["creator_credential"], made["spec_sha"], made["revision"])
    deadline = time.monotonic() + 120
    while service.tournaments().status(made["tournament_id"])["status"] != "completed" and time.monotonic() < deadline:
        time.sleep(0.1)
    return made


def ranked(service, id):
    return [item for item in service.list_rankings(tournament_id=id)["items"]]


def test_a_completed_line_up_tournament_is_ranked_without_any_client(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 1)
    try:
        made = finish(service, spec())
        deadline = time.monotonic() + 30
        while not service.get_tournament_ratings(made["tournament_id"]) and time.monotonic() < deadline:
            time.sleep(0.1)
        fits = service.get_tournament_ratings(made["tournament_id"])
        assert len(fits) == 1 and ranked(service, made["tournament_id"])
        # Idempotent: the automatic fit happens once; asking again changes nothing.
        assert service.tournaments().auto_fit(made["tournament_id"]) is False
        assert service.get_tournament_ratings(made["tournament_id"]) == fits
        # A solo tournament compares scores only and never enters the Elo rankings.
        solo = finish(service, spec(players=1, name="solo"))
        time.sleep(0.3)
        assert service.get_tournament_ratings(solo["tournament_id"]) == []
        assert service.tournaments().needs_auto_fit(solo["tournament_id"]) is False
        # The creator can still fit again with another profile.
        other = {**recommended_fit(), "ridge_lambda": 1.0}
        service.fit_tournament_ratings(made["tournament_id"], made["creator_credential"],
                                       service.get_tournament(made["tournament_id"])["revision"], other)
        assert len(service.get_tournament_ratings(made["tournament_id"])) == 2
    finally:
        service.close_tournaments()


def test_the_startup_recovery_fits_a_tournament_completed_before_a_crash(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 2)
    try:
        # A crash between completion and the fit: here the automatic fit is switched off while the games run.
        service.tournaments().auto_fit_spec = None
        made = finish(service, spec())
        assert service.get_tournament_ratings(made["tournament_id"]) == []
    finally:
        service.close_tournaments()
    restarted = PlayService(tmp_path, seed_factory=lambda: 2)
    try:
        assert restarted.recover_tournaments() == [made["tournament_id"]]
        assert len(restarted.get_tournament_ratings(made["tournament_id"])) == 1
        assert restarted.recover_tournaments() == []
        assert len(restarted.get_tournament_ratings(made["tournament_id"])) == 1
    finally:
        restarted.close_tournaments()


def test_the_http_server_runs_the_recovery_when_it_starts(tmp_path):
    from fastapi.testclient import TestClient
    from boardcraft.server.api import create_app
    first = PlayService(tmp_path, seed_factory=lambda: 3)
    first.tournaments().auto_fit_spec = None
    made = finish(first, spec())
    first.close_tournaments()
    service = PlayService(tmp_path, seed_factory=lambda: 3)
    with TestClient(create_app(service, run_bots=False)) as client:
        deadline = time.monotonic() + 30
        while not client.get(f"/api/tournaments/{made['tournament_id']}/ratings").json() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert len(client.get(f"/api/tournaments/{made['tournament_id']}/ratings").json()) == 1
