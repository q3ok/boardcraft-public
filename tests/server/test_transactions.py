"""Game computation and replay run after coherent database reads close."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import threading
import pytest
from boardcraft.contracts import GameConfig
from boardcraft.server.service import PlayService


def human_table(service):
    made = service.create_game(GameConfig('dice_circuit', 'base', 1), ['human'])
    joined = service.join_game(made['table_id'], 0)
    return made, joined['credential']


def test_held_bootstrap_leaves_unrelated_table_free_to_mutate(tmp_path, monkeypatch):
    seeds = iter((0, 1))
    service = PlayService(tmp_path, seed_factory=lambda: next(seeds))
    lobby, _ = human_table(service)
    playing, token = human_table(service)
    service.start_game(playing['table_id'], playing['creator_credential'])
    actions = service.get_actions(playing['table_id'], token)
    module = service.module('dice_circuit')
    original = module.new
    blocked, release, other_done = threading.Event(), threading.Event(), threading.Event()
    def new(config, seed):
        if seed == 0:
            blocked.set()
            assert release.wait(10)
        return original(config, seed)
    monkeypatch.setattr(module, 'new', new)
    def other():
        result = service.apply_action(playing['table_id'], token, actions['decision_id'], actions['actions'][0]['id'])
        other_done.set()
        return result
    with ThreadPoolExecutor(2) as pool:
        start = pool.submit(service.start_game, lobby['table_id'], lobby['creator_credential'])
        try:
            assert blocked.wait(5)
            mutation = pool.submit(other)
            assert other_done.wait(5), 'Unrelated mutation waited for held bootstrap'
        finally:
            release.set()
        assert start.result()['status'] == 'playing'
        assert mutation.result()['status'] == 'playing'


@pytest.mark.parametrize('operation', ('get_view', 'get_actions', 'get_result', 'bot_work', 'get_replay'))


def test_read_computation_has_no_active_database_transaction(tmp_path, monkeypatch, operation):
    service = PlayService(tmp_path, seed_factory=lambda: 0)
    made, token = human_table(service)
    table = made['table_id']
    service.start_game(table, made['creator_credential'])
    if operation == 'get_replay':
        while service.get_result(table)['result'] is None:
            actions = service.get_actions(table, token)
            service.apply_action(table, token, actions['decision_id'], actions['actions'][0]['id'])
    active = []
    transaction, restore = service.store.transaction, service._engine
    @contextmanager
    def tracked(write=False):
        with transaction(write) as db:
            active.append(db)
            try:
                yield db
            finally:
                active.remove(db)
    def engine(state):
        assert not any(db.in_transaction for db in active), 'Engine work retained a database transaction'
        return restore(state)
    monkeypatch.setattr(service.store, 'transaction', tracked)
    monkeypatch.setattr(service, '_engine', engine)
    if operation == 'bot_work':
        service.bot_work(table)
    else:
        getattr(service, operation)(table, token)


def test_two_instances_start_one_lobby_exactly_once(tmp_path, monkeypatch):
    first = PlayService(tmp_path, seed_factory=lambda: 0)
    second = PlayService(tmp_path)
    made, token = human_table(first)
    barrier = threading.Barrier(2)
    module = first.module('dice_circuit')
    original = module.new
    def synchronized(config, seed):
        barrier.wait(timeout=5)
        return original(config, seed)
    monkeypatch.setattr(module, 'new', synchronized)
    def start(service):
        try:
            return service.start_game(made['table_id'], made['creator_credential'])
        except ValueError:
            return None
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(start, (first, second)))
    assert sum(result is not None for result in results) == 1
    events = first.get_events(made['table_id'], token)
    assert [event['sequence'] for event in events['events']] == list(range(1, events['next_cursor'] + 1))


def test_dice_view_and_actions_track_only_the_viewers_perspective(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 9)
    made = service.create_game(GameConfig('dice_circuit', 'base', 3), ['human'] * 3)
    table = made['table_id']
    tokens = [service.join_game(table, seat)['credential'] for seat in range(3)]
    service.start_game(table, made['creator_credential'])
    for _ in range(400):
        moves = [service.get_actions(table, token) for token in tokens]
        # Surrender is the only command of a seated person; it never takes part in the turn.
        assert all([c['kind'] for c in move['commands']] == ['resign'] for move in moves)
        actor = next(seat for seat, move in enumerate(moves) if move['actions'])
        move = moves[actor]
        before = service.get_view(table)
        service.apply_action(table, tokens[actor], move['decision_id'], move['actions'][0]['id'])
        after = service.get_view(table)
        if before['observation'] == after['observation']:
            assert before == {**after, 'server_time': before['server_time']}
            assert after['revision'] == after['observation']['revision']
            assert after['event_cursor'] == service.get_events(table)['next_cursor']
            assert 'expected_revision' not in service.get_actions(table)
            return
    pytest.fail('Expected a private Dice decision')
