"""Open management (a server on a trusted network): any client manages any table, tournament and knockout
without the creator credential, and every list and detail says so. The public server keeps requiring credentials."""
import time

import pytest

from boardcraft.contracts import GameConfig
from boardcraft.server.service import PlayService
from boardcraft.server.table_admin import delete_table, set_autostart
from boardcraft.server.views import AccessDenied

from test_global_rankings import spec, wait


@pytest.mark.parametrize("open_management", [True, False])
def test_tournaments_and_knockouts(tmp_path, open_management):
    service = PlayService(tmp_path, seed_factory=lambda: 6, open_management=open_management)
    try:
        made = service.create_tournament(spec(name="saved"))
        id = made["tournament_id"]
        detail = service.get_tournament(id)
        assert detail["can_manage"] is open_management
        assert detail["management"] == ("open" if open_management else "credential")
        assert all(t["can_manage"] is open_management for t in service.list_tournaments())
        if not open_management:
            with pytest.raises(AccessDenied):
                service.start_tournament(id, None, made["spec_sha"], 0)
            with pytest.raises(AccessDenied):
                service.delete_tournament(id, "someone else", 0)
            # The creator still can.
            service.start_tournament(id, made["creator_credential"], made["spec_sha"], 0)
        else:
            # Any client: no credential at all; the revision still guards against stale clicks.
            service.start_tournament(id, None, made["spec_sha"], 0)
        assert wait(lambda: service.get_tournament(id)["status"] == "completed")
        if open_management:
            service.delete_tournament(id, None, service.get_tournament(id)["revision"])
            assert service.tournaments().status(id)["status"] == "deleted"
        ko = service.knockouts().create({"name": "ko", "config": {"game_id": "dice_circuit", "variant": "base", "players": 2},
                                         "candidates": ["greedy", "random"], "sparring": ["random"], "games": 4, "keep": 0.5, "seed": 1})
        status = service.knockouts().status(ko["knockout_id"])
        assert status["can_manage"] is open_management
        if open_management:
            assert service.knockouts().start(ko["knockout_id"], None, 0)["status"] == "running"
        else:
            with pytest.raises(AccessDenied):
                service.knockouts().start(ko["knockout_id"], None, 0)
    finally:
        service.close_tournaments()


@pytest.mark.parametrize("open_management", [True, False])
def test_tables(tmp_path, open_management):
    service = PlayService(tmp_path, seed_factory=lambda: 7, open_management=open_management)
    made = service.create_game(GameConfig("dice_circuit", "base", 2), ["human", "greedy"], visibility="listed")
    table = made["table_id"]
    listed = service.list_tables()
    view = service.get_view(table)
    if open_management:
        assert listed["can_manage"] is True and listed["items"][0]["can_manage"] is True
        assert view["can_manage"] is True
        assert set_autostart(service, table, None, True)["autostart"] is True
        assert delete_table(service, table, None)["deleted"] is True
        other = service.create_game(GameConfig("dice_circuit", "base", 1), ["random"], visibility="listed")
        service.start_game(other["table_id"], None)
        assert service.get_view(other["table_id"])["status"] in ("playing", "completed")
    else:
        # The public server answers exactly as before (no management fields) and requires the creator credential.
        assert "can_manage" not in listed and "can_manage" not in listed["items"][0] and "can_manage" not in view
        for attempt in (lambda: set_autostart(service, table, None, True), lambda: delete_table(service, table, None),
                        lambda: service.start_game(table, None)):
            with pytest.raises(AccessDenied):
                attempt()
        assert delete_table(service, table, made["creator_credential"])["deleted"] is True


def test_http_open_management(tmp_path):
    from fastapi.testclient import TestClient
    from boardcraft.server.api import create_app

    service = PlayService(tmp_path, seed_factory=lambda: 8, open_management=True)
    with TestClient(create_app(service, run_bots=False)) as client:
        made = client.post("/api/tournaments", json=spec(name="any")).json()
        detail = client.get(f"/api/tournaments/{made['tournament_id']}").json()
        assert detail["can_manage"] is True
        started = client.post(f"/api/tournaments/{made['tournament_id']}/start", json={"spec_sha": made["spec_sha"], "revision": 0})
        assert started.status_code == 200
        deadline = time.monotonic() + 120
        while client.get(f"/api/tournaments/{made['tournament_id']}").json()["status"] != "completed" and time.monotonic() < deadline:
            time.sleep(0.1)
        current = client.get(f"/api/tournaments/{made['tournament_id']}").json()
        assert client.post(f"/api/tournaments/{made['tournament_id']}/delete", json={"revision": current["revision"]}).status_code == 200
