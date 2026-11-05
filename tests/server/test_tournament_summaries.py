"""The tournament list reads summaries the server stores on every completed game: compact rows, the same results as
counting every match, matches read one page at a time, and a startup recovery for tournaments without a summary."""
import time

from boardcraft.server.api import create_app
from boardcraft.server.service import PlayService
from fastapi.testclient import TestClient


def spec(players=2, name="line-up", seeds=(5, 6), seats=("random", "greedy"), lineups=None):
    seats = list(seats)
    width = len(seats) if lineups is None else players
    body = {"name": name, "config": {"game_id": "dice_circuit", "variant": "base", "players": players},
            "rules_version": "1", "engine_version": "1", "seats": seats, "seeds": list(seeds),
            "sampler_seeds": [list(range(g * width, (g + 1) * width)) for g in range(len(seeds))],
            "rotations": list(range(players)), "match_count": len(seeds) * players,
            "schedule_cap": len(seeds) * players, "max_inflight": 2, "max_record_bytes": 50_000_000,
            "max_decisions": 200_000}
    if lineups is not None:
        body["lineups"] = lineups
    return body


def finish(service, body):
    made = service.create_tournament(body)
    service.start_tournament(made["tournament_id"], made["creator_credential"], made["spec_sha"], made["revision"])
    deadline = time.monotonic() + 120
    while service.tournaments().status(made["tournament_id"])["status"] != "completed" and time.monotonic() < deadline:
        time.sleep(0.1)
    return made["tournament_id"]


def counted(full):
    """Per-entrant results counted from every match, as the page used to do."""
    spec = full["spec"]
    n, rotations = spec["config"]["players"], spec["rotations"]
    rows = [{"games": 0, "wins": 0, "place": 0, "score": 0} for _ in spec["references"]]
    for m in full["matches"]:
        if m["status"] != "completed":
            continue
        rotation = rotations[m["ordinal"] % len(rotations)]
        lineup = spec["lineups"][m["ordinal"] // len(rotations)] if spec.get("lineups") else range(len(rows))
        for seat in range(n):
            row = rows[list(lineup)[(seat - rotation) % n]]
            row["games"] += 1
            row["wins"] += m["result"]["placements"][seat] == 1
            row["place"] += m["result"]["placements"][seat]
            row["score"] += m["result"]["scores"][seat]
    return rows


def test_list_rows_are_compact_and_equal_the_counted_results(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 1)
    try:
        fixed = finish(service, spec())
        pool = finish(service, spec(seats=("random", "greedy", "random"), lineups=[[0, 2], [1, 2]], name="pool"))
        solo = finish(service, spec(players=1, seats=("greedy",), seeds=(3, 4, 5), name="Solo: s · Greedy"))
        with TestClient(create_app(service, run_bots=False)) as client:
            rows = {row["tournament_id"]: row for row in client.get("/api/tournaments").json()}
            for id in (fixed, pool, solo):
                full = client.get(f"/api/tournaments/{id}").json()
                row = rows[id]
                assert "matches" not in row and "scores" not in row and "seeds" not in row["spec"]
                assert row["done"] == full["spec"]["match_count"] == row["spec"]["match_count"]
                assert row["results"] == counted(full)
                assert row["spec"]["seed_count"] == len(full["spec"]["seeds"])
                assert row["spec"]["first_seed"] == full["spec"]["seeds"][0]
                assert row["spec"]["pool"] == (id == pool)
                assert [e["choice_id"] for e in row["entrants"]] == [e["choice_id"] for e in full["entrants"]]
            # One tournament without its matches: the stored summary, with a solo tournament's scores by ordinal.
            brief = client.get(f"/api/tournaments/{solo}?matches=false").json()
            full = client.get(f"/api/tournaments/{solo}").json()
            assert "matches" not in brief
            assert brief["scores"] == [m["result"]["scores"][0] for m in sorted(full["matches"], key=lambda m: m["ordinal"])]
            assert client.get(f"/api/tournaments/{fixed}?matches=false").json()["scores"] is None
            # Matches one page at a time, in schedule order.
            page = client.get(f"/api/tournaments/{solo}/matches?offset=1&limit=1").json()
            assert page["total"] == 3 and [m["ordinal"] for m in page["items"]] == [1]
            assert page["items"][0]["match_id"] == sorted(full["matches"], key=lambda m: m["ordinal"])[1]["match_id"]
            assert client.get(f"/api/tournaments/{solo}/matches?limit=501").status_code == 422
    finally:
        service.close_tournaments()


def test_a_new_tournament_has_an_empty_summary_and_the_recovery_rebuilds_a_missing_one(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 1)
    try:
        made = service.create_tournament(spec())
        row = service.list_tournaments()[0]
        assert row["done"] == 0 and row["results"] == [{"games": 0, "wins": 0, "place": 0, "score": 0}] * 2
        done = finish(service, spec(name="done"))
        expected = service.tournaments().brief(done)
        # A tournament stored before summaries existed (or a lost row): the list still shows it, the recovery stores it.
        with service.store.transaction(True) as db:
            db.execute("DELETE FROM tournament_summaries")
        assert service.tournaments().brief(done) == expected
        assert sorted(service.recover_tournament_summaries()) == sorted([made["tournament_id"], done])
        assert service.recover_tournament_summaries() == []
        assert service.tournaments().brief(done) == expected
        # A summary that does not count every completion is recomputed by the recovery.
        with service.store.transaction(True) as db:
            db.execute("UPDATE tournament_summaries SET done=0 WHERE tournament_id=?", (done,))
        assert service.recover_tournament_summaries() == [done]
    finally:
        service.close_tournaments()
