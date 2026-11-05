"""Bounded background workers keep tables independent and fairly scheduled."""
import threading
from boardcraft.contracts import GameConfig
from boardcraft.server.bots import BotExecutor
from boardcraft.server.service import PlayService
from concurrent.futures import ThreadPoolExecutor


def test_worker_pool_rotates_between_ready_tables(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 0)
    for _ in range(2):
        made = service.create_game(GameConfig('dice_circuit', 'base', 1), ['random'])
        service.start_game(made['table_id'], made['creator_credential'])
    seen, ready = [], threading.Event()
    original = service.choose_bot
    def choose(work):
        seen.append(work.table_id)
        if len(seen) >= 2:
            ready.set()
        return original(work)
    service.choose_bot = choose
    worker = BotExecutor(service, workers=1)
    worker.start()
    try:
        assert ready.wait(5), 'Bot worker did not make progress'
    finally:
        worker.close()
    assert seen[0] != seen[1], 'A ready table must not monopolize the worker'


def test_slow_bot_does_not_hold_database_or_other_worker(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 0)
    tables = []
    for _ in range(2):
        made = service.create_game(GameConfig('dice_circuit', 'base', 1), ['random'])
        service.start_game(made['table_id'], made['creator_credential'])
        tables.append(made['table_id'])
    blocked, release, other = threading.Event(), threading.Event(), threading.Event()
    original = service.choose_bot
    def choose(work):
        if work.table_id == tables[0]:
            blocked.set()
            assert release.wait(5)
        else:
            other.set()
        return original(work)
    service.choose_bot = choose
    worker = BotExecutor(service, workers=2)
    worker.start()
    try:
        assert blocked.wait(5)
        assert service.get_view(tables[0])['status'] == 'playing'
        assert other.wait(5), 'Another table was blocked by the slow choice'
    finally:
        release.set()
        worker.close()


def test_slow_game_computation_does_not_hold_another_tables_writer(tmp_path):
    seeds = iter((0, 1))
    service = PlayService(tmp_path, seed_factory=lambda: next(seeds))
    sessions = []
    for _ in range(2):
        made = service.create_game(GameConfig('dice_circuit', 'base', 1), ['human'])
        joined = service.join_game(made['table_id'], 0)
        service.start_game(made['table_id'], made['creator_credential'])
        choices = service.get_actions(made['table_id'], joined['credential'])
        sessions.append((made['table_id'], joined['credential'], choices['decision_id'], choices['actions'][0]['id']))
    blocked, release, other_done = threading.Event(), threading.Event(), threading.Event()
    original = service._engine
    def restore(state):
        if state['seed'] == 0:
            blocked.set()
            assert release.wait(10), 'Controlled computation was not released'
        return original(state)
    service._engine = restore
    def other():
        result = service.apply_action(*sessions[1])
        other_done.set()
        return result
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(service.apply_action, *sessions[0])
        try:
            assert blocked.wait(5)
            second = pool.submit(other)
            assert other_done.wait(5), 'Unrelated table waited for held game computation'
        finally:
            release.set()
        assert first.result()['observation']['revision'] > 0
        assert second.result()['observation']['revision'] > 0


def test_rentcraft_simulator_runs_in_bounded_worker_while_mcp_can_observe(tmp_path, monkeypatch):
    import asyncio
    import json
    from mcp.client import Client
    from boardcraft.games.rentcraft.bots.adapter import RentcraftBot
    from boardcraft.mcp.server import create_server
    service = PlayService(tmp_path, seed_factory=lambda: 7)
    made = service.create_game(GameConfig('rentcraft', 'base', 2), ['simulator'] * 2)
    table = made['table_id']
    service.start_game(table, made['creator_credential'])
    entered, release = threading.Event(), threading.Event()
    workers = []
    def choose(bot, observation, actions):
        from boardcraft.contracts import BotChoice
        workers.append(threading.current_thread().name)
        entered.set()
        assert release.wait(10)
        return BotChoice(actions[0], 'Controlled simulator choice')
    monkeypatch.setattr(RentcraftBot, 'choose', choose)
    worker = BotExecutor(service, workers=1)
    worker.start()
    async def observe():
        async with Client(create_server(service)) as client:
            result = await client.call_tool('observe_game', {'table_id': table})
            assert json.loads(result.content[0].text)['status'] == 'playing'
    try:
        assert entered.wait(5)
        assert not worker.capacity.acquire(False)
        with service.store.transaction(True):
            pass
        asyncio.run(observe())
        assert len(workers) == 1 and workers[0].startswith('boardcraft-bot')
    finally:
        release.set()
        worker.close()
