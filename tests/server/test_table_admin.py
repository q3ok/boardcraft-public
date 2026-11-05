"""Creator-only table deletion (the replay stays) and lobby seat presentation (names and pieces)."""
import pytest

from boardcraft.contracts import GameConfig
from boardcraft.server.service import PlayService
from boardcraft.server.table_admin import delete_table, set_presentation
from boardcraft.server.views import AccessDenied, Conflict


def finished(service):
    made = service.create_game(GameConfig('dice_circuit', 'base', 1), ['random'], visibility='listed')
    service.start_game(made['table_id'], made['creator_credential'])
    while service.get_result(made['table_id'])['result'] is None:
        for work in service.bot_work(made['table_id']):
            service.apply_bot(work, service.choose_bot(work))
    return made


def test_deleting_a_finished_table_unlists_it_and_keeps_its_replay(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 4)
    made = finished(service)
    other = service.create_game(GameConfig('dice_circuit', 'base', 2), ['human', 'greedy'], visibility='listed')
    assert service.list_tables()['total'] == 2
    with pytest.raises(AccessDenied):
        delete_table(service, made['table_id'], other['creator_credential'])
    assert delete_table(service, made['table_id'], made['creator_credential'])['deleted'] is True
    listed = service.list_tables()
    assert listed['total'] == 1 and [t['table_id'] for t in listed['items']] == [other['table_id']]
    assert [r['table_id'] for r in service.list_replays()['items']] == [made['table_id']]
    assert service.get_replay(made['table_id'], position=0)['position'] == 0
    # A lobby table can be deleted by its creator, a table in play cannot.
    assert delete_table(service, other['table_id'], other['creator_credential'])['status'] == 'lobby'
    assert service.list_tables()['total'] == 0
    playing = service.create_game(GameConfig('dice_circuit', 'base', 2), ['greedy', 'random'], visibility='listed')
    service.start_game(playing['table_id'], playing['creator_credential'])
    if service.get_view(playing['table_id'])['status'] == 'playing':
        with pytest.raises(Conflict):
            delete_table(service, playing['table_id'], playing['creator_credential'])


def test_creator_names_seats_and_chooses_distinct_pieces_before_the_start(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 5)
    made = service.create_game(GameConfig('rentcraft', 'base', 2), ['human', 'random'], visibility='listed')
    table, owner = made['table_id'], made['creator_credential']
    result = set_presentation(service, table, owner, [{'seat': 0, 'display_name': 'Alice', 'token': 'lion'}, {'seat': 1, 'token': 'zebra'}])
    assert [(s['display_name'], s.get('token')) for s in result['seats']] == [('Alice', 'lion'), ('Random', 'zebra')] or \
        [s.get('token') for s in result['seats']] == ['lion', 'zebra']
    assert result['seats'][0]['display_name'] == 'Alice'
    item = service.list_tables()['items'][0]
    assert [s.get('token') for s in item['seats']] == ['lion', 'zebra'] and item['seats'][0]['display_name'] == 'Alice'
    with pytest.raises(ValueError, match='own piece'):
        set_presentation(service, table, owner, [{'seat': 0, 'token': 'zebra'}])
    with pytest.raises(ValueError):
        set_presentation(service, table, owner, [{'seat': 0, 'display_name': '<script>'}])
    with pytest.raises(AccessDenied):
        set_presentation(service, table, None, [{'seat': 0, 'display_name': 'Mallory'}])
    seat = service.join_game(table, 0)['credential']
    service.start_game(table, owner)
    assert service.get_view(table, seat)['seats'][0]['display_name'] == 'Alice'
    with pytest.raises(Conflict):
        set_presentation(service, table, owner, [{'seat': 0, 'display_name': 'Later'}])


def test_a_combined_player_seat_is_accepted_for_a_rentcraft_table(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 6)
    made = service.create_game(GameConfig('rentcraft', 'base', 2), ['hybrid:offers=random;rest=miser', 'miser'], visibility='listed')
    assert made['seats'][0]['bot_ref']['builtin_kind'] == 'hybrid'


def test_the_table_list_numbers_tables_and_shows_the_round_in_play(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 8)
    first = service.create_game(GameConfig('dice_circuit', 'base', 2), ['human', 'greedy'], visibility='listed')
    made = service.create_game(GameConfig('dice_circuit', 'base', 1), ['greedy'], visibility='listed')
    items = service.list_tables()['items']
    assert [t['number'] for t in items] == [1, 2] and 'round' not in items[0]
    service.start_game(made['table_id'], made['creator_credential'])
    rounds = []
    while service.get_result(made['table_id'])['result'] is None:
        for work in service.bot_work(made['table_id']):
            service.apply_bot(work, service.choose_bot(work))
        item = next(t for t in service.list_tables()['items'] if t['table_id'] == made['table_id'])
        rounds.append((item.get('round'), item.get('rounds')))
    assert rounds[0] == (1, 6) and rounds[-1][1] == 6
    assert [r for r, _ in rounds] == sorted(r for r, _ in rounds) and rounds[-2][0] == 6
    assert next(t for t in service.list_tables()['items'] if t['table_id'] == first['table_id'])['number'] == 1


def test_a_rentcraft_table_in_play_lists_its_round():
    from boardcraft.registry import get_game
    module = get_game('rentcraft')
    engine = module.new(GameConfig('rentcraft', 'base', 2), 3)
    assert module.table_round(engine)[0] == 1


def test_one_table_summary_carries_its_number_for_the_game_bar(tmp_path):
    from fastapi.testclient import TestClient
    from boardcraft.server.api import create_app
    service = PlayService(tmp_path, seed_factory=lambda: 9)
    service.create_game(GameConfig('dice_circuit', 'base', 2), ['human', 'greedy'], visibility='listed')
    made = service.create_game(GameConfig('dice_circuit', 'base', 2), ['human', 'greedy'], visibility='listed')
    with TestClient(create_app(service, run_bots=False)) as client:
        body = client.get(f"/api/tables/{made['table_id']}/summary").json()
        assert body['number'] == 2 and body['game_id'] == 'dice_circuit'
        assert client.get('/api/tables/' + 'f' * 32 + '/summary').json() == {'table_id': 'f' * 32, 'number': None}


def test_an_armed_table_is_started_by_the_server_when_the_last_seat_is_taken(tmp_path):
    from boardcraft.server.table_admin import set_autostart
    service = PlayService(tmp_path, seed_factory=lambda: 10)
    made = service.create_game(GameConfig('dice_circuit', 'base', 2), ['human', 'greedy'], visibility='listed')
    table, owner = made['table_id'], made['creator_credential']
    with pytest.raises(AccessDenied):
        set_autostart(service, table, None, True)
    assert set_autostart(service, table, owner, True)['status'] == 'lobby'  # the human seat is still free
    assert service.autostart_if_ready(table) is False
    service.join_game(table, 0)
    assert service.get_view(table)['status'] == 'playing'
    assert service.autostart_if_ready(table) is False  # idempotent: already started
    # Disarmed, a full table waits for the creator's Start.
    other = service.create_game(GameConfig('dice_circuit', 'base', 2), ['human', 'greedy'], visibility='listed')
    set_autostart(service, other['table_id'], other['creator_credential'], True)
    set_autostart(service, other['table_id'], other['creator_credential'], False)
    service.join_game(other['table_id'], 0)
    assert service.get_view(other['table_id'])['status'] == 'lobby'


def test_the_server_poller_starts_an_armed_table_of_bots(tmp_path):
    import time
    from fastapi.testclient import TestClient
    from boardcraft.server.api import create_app
    service = PlayService(tmp_path, seed_factory=lambda: 11)
    made = service.create_game(GameConfig('dice_circuit', 'base', 2), ['greedy', 'random'], visibility='listed')
    with TestClient(create_app(service, run_bots=True)) as client:
        answer = client.post(f"/api/tables/{made['table_id']}/autostart", json={'on': True},
                             headers={'Authorization': f"Bearer {made['creator_credential']}"}).json()
        assert answer['autostart'] is True
        deadline = time.monotonic() + 20
        while service.get_view(made['table_id'])['status'] == 'lobby' and time.monotonic() < deadline:
            time.sleep(0.05)
        assert service.get_view(made['table_id'])['status'] != 'lobby'
