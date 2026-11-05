"""The replay library and the tournament lists as the shared design shows them: newest first (ordered by the server),
player names instead of bot kinds, the search done by the server, and selected replays deleted on a private server."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from boardcraft.contracts import GameConfig
from boardcraft.server import knockout as knockout_module
from boardcraft.server.api import create_app
from boardcraft.server.service import PlayService
from boardcraft.server.views import AccessDenied
from tests.server.test_tournament_summaries import finish, spec


def names(item):
    return [p['display_name'] for p in sorted(item['participants'], key=lambda p: p['seat'])]


def test_the_tournament_list_is_newest_first_with_the_creation_time(tmp_path):
    service = PlayService(tmp_path)
    try:
        ids = [service.create_tournament(spec(name=f't{i}'))['tournament_id'] for i in range(3)]
        listed = service.list_tournaments()
        assert [t['tournament_id'] for t in listed] == ids[::-1]
        created = [t['created'] for t in listed]
        assert all(type(c) is float for c in created) and created == sorted(created, reverse=True)
        with TestClient(create_app(service, run_bots=False)) as client:
            assert [t['tournament_id'] for t in client.get('/api/tournaments').json()] == ids[::-1]
    finally:
        service.close_tournaments()


def test_the_knockout_list_is_newest_first(tmp_path, monkeypatch):
    service = PlayService(tmp_path)
    clock = iter([100.0, 300.0, 200.0])
    monkeypatch.setattr(knockout_module.time, 'time', lambda: next(clock))
    body = {"name": "pick", "config": {"game_id": "dice_circuit", "variant": "base", "players": 2},
            "candidates": ["random", "greedy", "strategist"], "sparring": ["random"], "games": 24, "keep": 0.5,
            "min_candidates": 1, "metric": "wins", "seed": 1, "max_inflight": 1}
    try:
        made = [service.knockouts().create({**body, "name": f"k{i}"})["knockout_id"] for i in range(3)]
        assert [k["knockout_id"] for k in service.knockouts().list()] == [made[1], made[2], made[0]]
    finally:
        service.close_tournaments()


def test_tournament_replays_name_the_players_newest_first_and_the_server_filters(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 1)
    try:
        alpha = finish(service, spec(name="alpha", seats=("random", "greedy")))
        beta = finish(service, spec(name="beta", seats=("random", "greedy")))
        page = service.list_replays(limit=100)
        assert page['total'] == 8
        # Newest tournament first; inside a tournament the later matches first.
        assert [i['tournament_id'] for i in page['items']] == [beta] * 4 + [alpha] * 4
        assert [i['ordinal'] for i in page['items'][:4]] == [3, 2, 1, 0]
        for item in page['items']:
            assert sorted(names(item)) == ['Greedy bot', 'Random bot']
            assert item['tournament_name'] in ('alpha', 'beta') and type(item['played_at']) is float
        assert service.list_replays(tournament='ALP')['total'] == 4
        assert service.list_replays(q='nobody')['total'] == 0
        assert service.list_replays(q='greedy')['total'] == 8
        wins = service.list_replays(q='greedy', won=True, limit=100)['items']
        assert wins and all(names(i)[i['result']['placements'].index(1)] == 'Greedy bot' for i in wins)
        others = service.list_replays(q='random', won=True)['total']
        assert len(wins) + others == 8
        with TestClient(create_app(service, run_bots=False)) as client:
            body = client.get('/api/replays?q=greedy&won=1&tournament=be&limit=20').json()
            assert body['total'] == sum(1 for i in wins if i['tournament_id'] == beta)
            assert client.get('/api/replays?won=maybe').status_code in (400, 422)
    finally:
        service.close_tournaments()


def test_older_tournaments_get_names_and_a_creation_time_from_the_startup_recovery(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 1)
    try:
        old = finish(service, spec(name="old", seats=("random", "greedy")))
        new = service.create_tournament(spec(name="new"))['tournament_id']
        # Saved by an earlier server: no names and no creation time.
        with service.store.transaction(True) as db:
            db.execute('DELETE FROM tournament_labels')
            db.execute('DELETE FROM tournament_created')
        assert {p['display_name'] for i in service.list_replays()['items'] for p in i['participants']} <= {
            'Random', 'Greedy', 'Random bot', 'Greedy bot'}
        service.recover_tournaments()
        assert [t['tournament_id'] for t in service.list_tournaments()] == [new, old]
        assert all(sorted(names(i)) == ['Greedy bot', 'Random bot'] for i in service.list_replays()['items'])
        with service.store.transaction() as db:
            labels = json.loads(db.execute('SELECT labels_json FROM tournament_labels WHERE tournament_id=?', (old,)).fetchone()[0])
        assert sorted(labels.values()) == ['Greedy bot', 'Random bot']
    finally:
        service.close_tournaments()


def test_a_model_seat_is_saved_with_its_name(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 1)
    try:
        made = service.create_tournament(spec(seats=("random", "strategist")))
        with service.store.transaction() as db:
            labels = json.loads(db.execute('SELECT labels_json FROM tournament_labels WHERE tournament_id=?',
                                           (made['tournament_id'],)).fetchone()[0])
        assert sorted(labels.values()) == ['Random bot', 'Strategist']
    finally:
        service.close_tournaments()


def finished_table(service):
    made = service.create_game(GameConfig('dice_circuit', 'base', 2), ['greedy', 'random'], visibility='listed')
    table = made['table_id']
    service.start_game(table, made['creator_credential'])
    while service.get_result(table)['result'] is None:
        for work in service.bot_work(table):
            service.apply_bot(work, service.choose_bot(work))
    return table


def test_selected_replays_are_deleted_on_a_private_server_and_leave_the_rankings(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 1, open_management=True)
    try:
        tournament = finish(service, spec(name="kept", seats=("random", "greedy")))
        table = finished_table(service)
        page = service.list_replays(limit=100)
        assert page['total'] == 5 and page['can_manage'] is True
        assert page['items'][0]['source'] == 'table'
        match = next(i for i in page['items'] if i['source'] == 'tournament')
        from boardcraft.server.leaderboard import _collect
        before = sum(len(rows) for rows in _collect(service)[0].values())
        with TestClient(create_app(service, run_bots=False)) as client:
            response = client.post('/api/replays/delete', json={'replays': [
                {'source': 'table', 'table_id': table},
                {'source': 'tournament', 'tournament_id': tournament, 'match_id': match['match_id']}]})
            assert response.status_code == 200 and response.json() == {'deleted': 2}
            after = client.get('/api/replays?limit=100').json()
        assert after['total'] == 3
        assert table not in {i.get('table_id') for i in after['items']}
        assert match['match_id'] not in {i.get('match_id') for i in after['items']}
        assert sum(len(rows) for rows in _collect(service)[0].values()) == before - 1
        # Deleting again changes nothing.
        assert service.delete_replays([{'source': 'table', 'table_id': table}]) == {'deleted': 0}
    finally:
        service.close_tournaments()


def test_a_public_server_does_not_delete_replays(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 1)
    table = finished_table(service)
    assert service.list_replays()['can_manage'] is False
    with pytest.raises(AccessDenied):
        service.delete_replays([{'source': 'table', 'table_id': table}])
    with TestClient(create_app(service, run_bots=False)) as client:
        assert client.post('/api/replays/delete', json={'replays': [{'source': 'table', 'table_id': table}]}).status_code == 403
        assert client.post('/api/replays/delete', json={'replays': []}).status_code in (400, 422)
    assert service.list_replays()['total'] == 1
