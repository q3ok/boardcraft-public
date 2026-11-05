"""K2: bounded asynchronous waits, hidden changes and resource cleanup."""
import asyncio
import threading
import pytest
from mcp2_synthetic import table


async def until(predicate):
    for _ in range(200):
        if predicate(): return
        await asyncio.sleep(.01)
    raise AssertionError('Condition was not reached')


def test_command_only_seat_is_actionable_and_observer_requires_changed_mode(tmp_path):
    service, tid, tokens = table(tmp_path)
    async def run():
        result = await service.wait_for_decision(tid, tokens[1], timeout=0)
        assert result['reason'] == 'actionable' and result['actions'] == [] and result['commands']
        with pytest.raises(ValueError):
            await service.wait_for_decision(tid, timeout=0)
        for timeout in (-1, 25.01, True, float('nan')):
            with pytest.raises(ValueError):
                await service.wait_for_decision(tid, tokens[0], timeout=timeout)
        for mode in ('wrong', None):
            with pytest.raises(ValueError):
                await service.wait_for_decision(tid, tokens[0], mode=mode, timeout=0)
    asyncio.run(run())


def test_hidden_transition_does_not_wake_other_seat_or_observer(tmp_path):
    service, tid, tokens = table(tmp_path)
    async def run():
        before = service.get_view(tid, tokens[1])
        public = service.get_view(tid)
        own = service.get_view(tid, tokens[0])
        waits = [asyncio.create_task(service.wait_for_decision(tid, token, mode='changed', after_change_token=base['change_token'], timeout=.15))
                 for token, base in [(tokens[1], before), (None, public)]]
        await until(lambda: service._wait_hub.count == 2)
        moved = await asyncio.to_thread(service.apply_action, tid, tokens[0], 'choice-0', 'choose')
        assert moved['decision'] is None
        assert [r['reason'] for r in await asyncio.gather(*waits)] == ['timeout', 'timeout']
        changed = await service.wait_for_decision(tid, tokens[0], mode='changed', after_change_token=own['change_token'], timeout=0)
        assert changed['reason'] == 'changed'
        assert service._wait_hub.count == 0
    asyncio.run(run())


def test_wait_limit_does_not_hold_transactions_and_cancellation_frees_capacity(tmp_path):
    service, tid, tokens = table(tmp_path)
    async def run():
        waits = [asyncio.create_task(service.wait_for_decision(tid, mode='changed')) for _ in range(32)]
        try:
            await until(lambda: service._wait_hub.count == 32)
            with pytest.raises(ValueError):
                await service.wait_for_decision(tid, mode='changed', timeout=0)
            # A writer and a move complete while every waiter is sleeping.
            with service.store.transaction(True) as db:
                assert db.execute('SELECT count(*) FROM tables').fetchone()[0] == 1
            await asyncio.wait_for(asyncio.to_thread(service.apply_action, tid, tokens[0], 'choice-0', 'choose'), 2)
        finally:
            for waiter in waits: waiter.cancel()
            await asyncio.gather(*waits, return_exceptions=True)
        assert service._wait_hub.count == 0
        assert (await service.wait_for_decision(tid, mode='changed', timeout=0))['reason'] == 'timeout'
    asyncio.run(run())


def test_registered_before_check_has_no_lost_wakeup(tmp_path, monkeypatch):
    service, tid, tokens = table(tmp_path)
    before = service.get_view(tid, tokens[0])
    checked, release = threading.Event(), threading.Event()
    original = service.get_view
    first = [True]
    def gated(*args):
        result = original(*args)
        if first[0]:
            first[0] = False
            checked.set()
            assert release.wait(2)
        return result
    monkeypatch.setattr(service, 'get_view', gated)
    async def run():
        waiter = asyncio.create_task(service.wait_for_decision(tid, tokens[0], mode='changed', after_change_token=before['change_token'], timeout=1))
        try:
            assert await asyncio.to_thread(checked.wait, 2)
            assert service._wait_hub.count == 1
            await asyncio.to_thread(service.apply_action, tid, tokens[0], 'choice-0', 'choose')
        finally:
            release.set()
        assert (await asyncio.wait_for(waiter, .2))['reason'] == 'changed'
    asyncio.run(run())


def test_completed_and_inactive_are_terminal_wait_reasons(tmp_path):
    service, tid, tokens = table(tmp_path)
    async def run():
        with service.store.transaction(True) as db:
            state = service.store.load(db, tid)
            state['snapshot']['inactive'] = [0]
            service.store.save(db, tid, state)
        assert (await service.wait_for_decision(tid, tokens[0], timeout=0))['reason'] == 'seat_inactive'
        with service.store.transaction(True) as db:
            state = service.store.load(db, tid)
            state['snapshot']['inactive'] = []
            service.store.save(db, tid, state)
        service.apply_action(tid, tokens[0], 'choice-0', 'choose')
        service.submit_command(tid, tokens[1], service._read_state(tid)['revision'], 'choose', {}, 'choice-1')
        assert (await service.wait_for_decision(tid, timeout=0, mode='changed'))['reason'] == 'completed'
    asyncio.run(run())


def test_existing_rentcraft_elimination_and_waits_never_renew_deadline(tmp_path):
    from boardcraft.contracts import GameConfig
    from boardcraft.server.service import PlayService
    now = [1000.]
    service = PlayService(tmp_path, seed_factory=lambda: 19, clock=lambda: now[0])
    made = service.create_game(GameConfig('rentcraft', 'base', 3), ['agent'] * 3)
    tid = made['table_id']
    tokens = [service.join_game(tid, seat)['credential'] for seat in range(3)]
    started = service.start_game(tid, made['creator_credential'])
    actor = started['observation']['data']['acting_seat']
    before = service.get_view(tid, tokens[actor])
    deadline = before['deadline']
    async def run():
        now[0] += 10
        for _ in range(3):
            service.get_view(tid, tokens[actor])
            service.get_actions(tid, tokens[actor])
            result = await service.wait_for_decision(tid, tokens[actor], timeout=0)
            assert result['deadline'] == deadline
        state = service._read_state(tid)
        assert state['deadline'] == deadline
        service.submit_command(tid, tokens[actor], state['revision'], 'resign', {})
        result = await service.wait_for_decision(tid, tokens[actor], timeout=0)
        assert result['status'] == 'playing' and result['reason'] == 'seat_inactive'
    asyncio.run(run())
