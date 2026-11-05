"""Instance-local, opt-in table discovery never exposes stored game state."""
from dataclasses import asdict, replace
import json
import subprocess
import sys
from contextlib import contextmanager
import pytest
from fastapi.testclient import TestClient
from boardcraft.contracts import GameConfig
from boardcraft.server.api import create_app
from boardcraft.server.service import PlayService
from boardcraft.server.views import Conflict


def test_dice_agent_does_not_require_live_seat_operations(tmp_path):
    service = PlayService(tmp_path)
    made = service.create_game(GameConfig('dice_circuit', 'base', 1), ['agent'])
    state = service._read_state(made['table_id'])
    assert state['seats'][0]['control_kind'] == 'agent'
    assert state['seats'][0]['bot_ref'] is None and state['seats'][0]['hash'] is None
    assert not state.get('live')


def test_core_table_directory_does_not_require_optional_transport_dependencies(tmp_path):
    source = '''
import sys
sys.modules['pydantic'] = None
sys.modules['fastapi'] = None
sys.modules['mcp'] = None
from boardcraft.server.service import PlayService
service = PlayService(sys.argv[1])
assert service.list_tables() == {'items': [], 'total': 0, 'offset': 0, 'limit': 25}
'''
    result = subprocess.run([sys.executable, '-I', '-B', '-c', source, str(tmp_path)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_invite_creation_preserves_clock_independence(tmp_path):
    def unused_clock():
        pytest.fail('Invite creation must not start or consult a play clock')
    service = PlayService(tmp_path, clock=unused_clock)
    assert service.create_game(GameConfig('dice_circuit', 'base', 1), ['human'])['table_id']


def test_large_valid_offset_returns_empty_page(tmp_path):
    service = PlayService(tmp_path)
    service.create_game(GameConfig('dice_circuit', 'base', 1), ['human'], visibility='listed')
    assert service.list_tables(offset=10**50) == {'items': [], 'total': 1, 'offset': 10**50, 'limit': 25}


@pytest.mark.parametrize('game,players', [('dice_circuit', 1), ('rentcraft', 2)])
def test_agent_role_join_action_and_directory_transitions(tmp_path, game, players):
    service = PlayService(tmp_path, seed_factory=lambda: 19)
    made = service.create_game(GameConfig(game, 'base', players), ['agent'] * players, visibility='listed')
    table = made['table_id']
    before = service.list_tables()['items'][0]
    assert before['seats'] == [{'seat': i, 'kind': 'agent', 'occupied': False, 'display_name': 'Agent'} for i in range(players)]
    assert service.get_view(table)['observation'] is None
    tokens = [service.join_game(table, i)['credential'] for i in range(players)]
    assert all(s['occupied'] for s in service.list_tables()['items'][0]['seats'])
    with pytest.raises(Conflict):
        service.join_game(table, 0)
    service.start_game(table, made['creator_credential'])
    assert service.list_tables(status='playing')['total'] == 1
    moves = [service.get_actions(table, token) for token in tokens]
    actor = next(i for i, move in enumerate(moves) if move['actions'])
    move = moves[actor]
    service.apply_action(table, tokens[actor], move['decision_id'], move['actions'][0]['id'])
    assert service.get_view(table, tokens[actor])['observation'] is not None
    if game == 'dice_circuit':
        assert 'live' not in service.get_view(table)
        assert service.get_actions(table, tokens[actor])['commands'] == []
        while service.get_result(table)['result'] is None:
            move = service.get_actions(table, tokens[0])
            service.apply_action(table, tokens[0], move['decision_id'], move['actions'][0]['id'])
        assert service.list_tables(status='completed')['total'] == 1


def test_directory_invite_legacy_isolation_filters_and_detached_safe_projection(tmp_path, monkeypatch):
    service = PlayService(tmp_path / 'one', seed_factory=lambda: 3, clock=lambda: 10)
    config = GameConfig('dice_circuit', 'base', 1)
    legacy = service.create_game(config, ['human'])
    service.create_game(config, ['random'], visibility='invite')
    listed = [service.create_game(config, ['human'], visibility='listed') for _ in range(3)]
    service.create_game(GameConfig('rentcraft', 'base', 2), ['human'] * 2, visibility='listed')
    with service.store.transaction() as db:
        baseline = db.iterdump()
        before = '\n'.join(baseline)
        assert db.execute('SELECT count(*) FROM table_directory WHERE table_id=?', (legacy['table_id'],)).fetchone()[0] == 0
    def forbidden(*args, **kwargs):
        pytest.fail('Directory reads must not restore engines or inspect bot availability')
    monkeypatch.setattr(service, '_engine', forbidden)
    monkeypatch.setattr(service, '_provider_resolve', forbidden)
    page = service.list_tables(game_id='dice_circuit', status='lobby', offset=1, limit=1)
    assert page['total'] == 3 and page['offset'] == 1 and page['limit'] == 1
    assert page['items'][0]['table_id'] == sorted(x['table_id'] for x in listed)[1]
    item = page['items'][0]
    # The number is the table's place in the directory (a public list detail, not table data).
    assert set(item) == {'table_id', 'game_id', 'variant', 'players', 'status', 'seats', 'number'}
    assert set(item['seats'][0]) == {'seat', 'kind', 'occupied', 'display_name'}
    serialized = json.dumps(service.list_tables())
    assert not any(secret in serialized for secret in ('credential', 'hash', 'snapshot', 'seed', 'bot_ref', str(tmp_path)))
    item['seats'][0]['display_name'] = 'Changed'
    assert service.list_tables()['items'][0]['seats'][0]['display_name'] == 'Human'
    with service.store.transaction() as db:
        assert '\n'.join(db.iterdump()) == before
    assert PlayService(tmp_path / 'two').list_tables()['total'] == 0
    assert PlayService(tmp_path / 'one').list_tables()['total'] == 4


@pytest.mark.parametrize('kwargs', [{'offset': True}, {'offset': -1}, {'offset': 1.0}, {'limit': False}, {'limit': 0}, {'limit': 101}, {'limit': '2'}, {'status': 'running'}, {'game_id': 'dice-circuit'}])
def test_directory_strict_inputs(tmp_path, kwargs):
    service = PlayService(tmp_path)
    with pytest.raises(ValueError):
        service.list_tables(**kwargs)


def test_directory_http_visibility_and_query_validation(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 0)
    with TestClient(create_app(service, run_bots=False)) as client:
        body = {'config': {'game_id': 'dice_circuit', 'variant': 'base', 'players': 1}, 'seats': ['agent'], 'visibility': 'listed'}
        assert client.post('/api/tables', json=body).status_code == 200
        assert client.get('/api/tables?limit=100').json()['total'] == 1
        for query in ('limit=101', 'limit=true', 'limit=1.0', 'offset=-1', 'status=waiting', 'game_id=dice-circuit'):
            assert client.get('/api/tables?' + query).status_code >= 400
        assert client.post('/api/tables', json={**body, 'visibility': 'public'}).status_code == 422


def test_failed_move_and_start_cas_cannot_rewrite_directory(tmp_path, monkeypatch):
    service = PlayService(tmp_path, seed_factory=lambda: 0)
    made = service.create_game(GameConfig('dice_circuit', 'base', 1), ['human'], visibility='listed')
    table = made['table_id']
    token = service.join_game(table, 0)['credential']
    commit = service._commit_start
    captured = []
    def hold(prepared):
        captured.append(prepared)
        return False
    monkeypatch.setattr(service, '_commit_start', hold)
    with pytest.raises(Conflict):
        service.start_game(table, made['creator_credential'])
    assert service.list_tables()['items'][0]['status'] == 'lobby'
    assert commit(captured[0])
    before = service.list_tables()
    assert not commit(captured[0])
    assert service.list_tables() == before
    state = service._read_state(table)
    engine = service._engine(state)
    move = service.get_actions(table, token)
    prepared = service._prepare_apply(table, state, engine, 0, move['decision_id'], move['actions'][0]['id'], 'Fixture')
    assert service._commit_apply(prepared)
    before = service.list_tables()
    stale = replace(prepared, payload=prepared.payload.replace('playing', 'completed'))
    assert not service._commit_apply(stale)
    assert service.list_tables() == before


def test_reserved_agent_catalog_choice(tmp_path):
    service = PlayService(tmp_path)
    original = service.bot_catalog.list_bots('dice_circuit')[0]
    class Catalog:
        def list_bots(self, game_id):
            return [replace(original, choice_id='agent')]
    service.bot_catalog = Catalog()
    with pytest.raises(ValueError, match='reserved'):
        service.list_bots('dice_circuit')


def test_directory_failure_rolls_back_create_join_and_start(tmp_path, monkeypatch):
    from boardcraft.server import directory
    service = PlayService(tmp_path, seed_factory=lambda: 0)
    config = GameConfig('dice_circuit', 'base', 1)
    def fail(*args):
        raise ValueError('Synthetic directory write failure')
    with monkeypatch.context() as patch:
        patch.setattr(directory, 'insert', fail)
        with pytest.raises(ValueError, match='Synthetic'):
            service.create_game(config, ['human'], visibility='listed')
    with service.store.transaction() as db:
        assert db.execute('SELECT count(*) FROM tables').fetchone()[0] == 0
    made = service.create_game(config, ['human'], visibility='listed')
    table = made['table_id']
    before = service._read_state(table)
    with monkeypatch.context() as patch:
        patch.setattr(directory, 'update', fail)
        with pytest.raises(ValueError, match='Synthetic'):
            service.join_game(table, 0)
    assert service._read_state(table) == before
    assert not service.list_tables()['items'][0]['seats'][0]['occupied']
    service.join_game(table, 0)
    before = service._read_state(table)
    with monkeypatch.context() as patch:
        patch.setattr(directory, 'write_summary', fail)
        with pytest.raises(ValueError, match='Synthetic'):
            service.start_game(table, made['creator_credential'])
    assert service._read_state(table) == before
    assert service.list_tables()['items'][0]['status'] == 'lobby'
    with service.store.transaction() as db:
        assert db.execute('SELECT count(*) FROM bootstrap').fetchone()[0] == 0


@pytest.mark.parametrize('visibility', ['invite', 'listed'])
def test_start_and_move_directory_preparation_does_not_hold_writer_transaction(tmp_path, monkeypatch, visibility):
    from boardcraft.server import directory
    service = PlayService(tmp_path, seed_factory=lambda: 0)
    made = service.create_game(GameConfig('dice_circuit', 'base', 1), ['human'], visibility=visibility)
    table = made['table_id']
    token = service.join_game(table, 0)['credential']
    transaction, decode, project = service.store.transaction, json.loads, directory.summary
    write_summary = directory.write_summary
    writers, projected, published = [], [], []
    @contextmanager
    def tracked(write=False, **kwargs):
        with transaction(write, **kwargs) as db:
            if write:
                writers.append(db)
            try:
                yield db
            finally:
                if write:
                    writers.remove(db)
    def outside_decode(*args, **kwargs):
        assert not writers, 'Full JSON decoding must precede the shared writer transaction'
        return decode(*args, **kwargs)
    def outside_project(*args, **kwargs):
        assert not writers, 'Directory projection must precede the shared writer transaction'
        projected.append(True)
        return project(*args, **kwargs)
    def atomic_summary(db, *args):
        assert db in writers and db.in_transaction
        published.append(True)
        return write_summary(db, *args)
    monkeypatch.setattr(service.store, 'transaction', tracked)
    monkeypatch.setattr(json, 'loads', outside_decode)
    monkeypatch.setattr(directory, 'summary', outside_project)
    monkeypatch.setattr(directory, 'write_summary', atomic_summary)
    service.start_game(table, made['creator_credential'])
    assert projected and published
    count = len(projected)
    writes = len(published)
    move = service.get_actions(table, token)
    view = service.apply_action(table, token, move['decision_id'], move['actions'][0]['id'])
    assert view['status'] == 'playing'
    assert len(projected) == count, 'An unchanged directory summary needs no preparation or write'
    assert len(published) == writes
    while service.get_result(table)['result'] is None:
        move = service.get_actions(table, token)
        service.apply_action(table, token, move['decision_id'], move['actions'][0]['id'])
    assert len(projected) == count + 1
    assert len(published) == writes + 1
    assert service.list_tables(status='completed')['total'] == (1 if visibility == 'listed' else 0)


def test_terminal_summary_commit_is_atomic_with_move_and_failed_cas(tmp_path, monkeypatch):
    from boardcraft.server import directory
    service = PlayService(tmp_path, seed_factory=lambda: 0)
    made = service.create_game(GameConfig('dice_circuit', 'base', 1), ['human'], visibility='listed')
    table = made['table_id']
    token = service.join_game(table, 0)['credential']
    service.start_game(table, made['creator_credential'])
    for _ in range(400):
        state = service._read_state(table)
        move = service.get_actions(table, token)
        prepared = service._prepare_apply(table, state, service._engine(state), 0,
                                          move['decision_id'], move['actions'][0]['id'], 'Fixture')
        if prepared.view['status'] == 'completed':
            break
        assert service._commit_apply(prepared)
    else:
        pytest.fail('Expected a terminal Dice Circuit move')
    before = service._read_state(table)
    events = service.get_events(table, token)
    listing = service.list_tables()
    def failed_publication(*args):
        raise ValueError('Synthetic terminal summary write failure')
    with monkeypatch.context() as patch:
        patch.setattr(directory, 'write_summary', failed_publication)
        assert not service._commit_apply(replace(prepared, expected_revision=prepared.expected_revision - 1))
        with pytest.raises(ValueError, match='Synthetic terminal'):
            service._commit_apply(prepared)
    assert service._read_state(table) == before
    assert service.get_events(table, token) == events
    assert service.list_tables() == listing
    assert service._commit_apply(prepared)
    assert service.list_tables(status='completed')['total'] == 1
    assert not service._commit_apply(prepared)


@pytest.mark.parametrize('game', ['dice_circuit', 'rentcraft'])
def test_module_owned_descriptions_are_detached_without_identity_changes(tmp_path, monkeypatch, game):
    service = PlayService(tmp_path)
    module = service.module(game)
    identity = asdict(module.info())
    descriptors = [asdict(item) for item in service.bot_catalog.list_bots(game)]
    assert isinstance(module.description, str) and module.description
    assert set(module.bot_descriptions) == set(module.info().bot_kinds)
    games = service.list_games()
    assert next(item for item in games if item['game_id'] == game)['description'] == module.description
    bots = service.list_bots(game)
    builtin = [item for item in bots if item['reference'].get('builtin_kind') is not None]
    assert builtin and all(item['description'] == module.bot_descriptions[item['reference']['builtin_kind']]
                           for item in builtin)
    assert all(isinstance(item['description'], str) and item['description'] for item in bots)
    bots[0]['description'] = 'Changed'
    assert service.list_bots(game)[0]['description'] != 'Changed'
    assert asdict(module.info()) == identity
    assert [asdict(item) for item in service.bot_catalog.list_bots(game)] == descriptors
    monkeypatch.delattr(type(module), 'description')
    monkeypatch.delattr(type(module), 'bot_descriptions')
    assert 'description' not in next(item for item in service.list_games() if item['game_id'] == game)
    after = service.list_bots(game)
    assert all('description' not in item for item in after if item['reference'].get('builtin_kind') is not None)
    assert all(item['description'] for item in after if item['reference'].get('builtin_kind') is None)
