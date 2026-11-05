"""M1 contract through actual loopback MCP and the HTTP relay."""
import asyncio
from contextlib import asynccontextmanager
import importlib.util
from pathlib import Path
import time
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette
from boardcraft.mcp.http import HttpPlayService
from boardcraft.mcp.transport import create_mcp_transport
from boardcraft.server.api import create_app
from boardcraft.server.service import PlayService
from test_streamable_http import Listener, call, rest, session

spec = importlib.util.spec_from_file_location('mcp2_fixture', Path(__file__).parents[1] / 'server' / 'mcp2_synthetic.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
KEY = 'test-transport-retry-key-000000000001'


@pytest.fixture
def host(tmp_path):
    service = fixture.SyntheticService(tmp_path, seed_factory=lambda: 37, bot_catalog=fixture.Catalog(), clock=lambda: 1000.)
    listener = Listener(create_app(service, run_bots=False))
    listener.service = service
    yield listener
    listener.close()


@pytest.mark.parametrize('relay', [False, True], ids=['native', 'relay'])
def test_all_mutations_replay_and_envelope_is_shared_by_rest_and_mcp(host, relay):
    listener = host
    if relay:
        route, run = create_mcp_transport(HttpPlayService(host.url))
        @asynccontextmanager
        async def lifespan(app):
            async with run(): yield
        listener = Listener(Starlette(routes=[route], lifespan=lifespan))
    async def run_test():
        async with session(listener) as client:
            tools = {t.name for t in (await client.list_tools()).tools}
            assert 'wait_for_decision' in tools and 'legal_actions' in tools
            assert 'preview_command' not in tools
            config = dict(game_id='mcp2_synthetic', variant='base', players=2)
            args = dict(config=config, seats=['human'] * 2, idempotency_key=KEY)
            made = await call(client, 'create_game', **args)
            assert await call(client, 'create_game', **args) == made
            tid = made['table_id']
            tokens = []
            for seat in range(2):
                joined = await call(client, 'join_game', table_id=tid, seat=seat, idempotency_key=KEY + str(seat))
                assert await call(client, 'join_game', table_id=tid, seat=seat, idempotency_key=KEY + str(seat)) == joined
                tokens.append(joined['credential'])
            started = await call(client, 'start_game', table_id=tid, credential=made['creator_credential'], idempotency_key=KEY)
            assert await call(client, 'start_game', table_id=tid, credential=made['creator_credential'], idempotency_key=KEY) == started
            before = await call(client, 'observe_game', table_id=tid, credential=tokens[0])
            assert before == rest(host, 'GET', f'/api/tables/{tid}/view', token=tokens[0])[1]
            ready = await call(client, 'wait_for_decision', table_id=tid, credential=tokens[1], timeout=0)
            assert ready['reason'] == 'actionable' and ready['actions'] == [] and ready['commands']
            arguments = dict(table_id=tid, credential=tokens[0], decision_id='choice-0', action_id='choose',
                             decision_token=before['decision_token'], idempotency_key=KEY)
            applied = await call(client, 'apply_action', **arguments)
            assert await call(client, 'apply_action', **arguments) == applied
            command = dict(table_id=tid, credential=tokens[1], decision_id='choice-1', kind='choose', params={},
                           decision_token=ready['decision_token'], idempotency_key=KEY)
            complete = await call(client, 'submit_command', **command)
            assert complete['status'] == 'completed'
            assert await call(client, 'submit_command', **command) == complete
            assert (await call(client, 'wait_for_decision', table_id=tid, mode='changed', timeout=0))['reason'] == 'completed'
            bad = await client.call_tool('apply_action', {**arguments, 'action_id': 'other'})
            assert bad.is_error and bad.structured_content['code'] == 'conflict'
    try:
        asyncio.run(run_test())
    finally:
        if relay: listener.close()


@pytest.mark.parametrize('relay', [False, True], ids=['native', 'relay'])
def test_full_wait_capacity_leaves_short_calls_and_cancels_on_disconnect(host, relay):
    made = host.service.create_game(fixture.GameConfig('mcp2_synthetic', 'base', 2), ['human'] * 2)
    tid = made['table_id']
    tokens = [host.service.join_game(tid, seat)['credential'] for seat in range(2)]
    host.service.start_game(tid, made['creator_credential'])
    listener = host
    if relay:
        route, run = create_mcp_transport(HttpPlayService(host.url))
        @asynccontextmanager
        async def lifespan(app):
            async with run(): yield
        listener = Listener(Starlette(routes=[route], lifespan=lifespan))
    async def run_test():
        async def wait():
            async with session(listener) as client:
                return await call(client, 'wait_for_decision', table_id=tid, mode='changed', timeout=25)
        pending = [asyncio.create_task(wait()) for _ in range(32)]
        try:
            end = time.monotonic() + 10
            while host.service._wait_hub.count < 32:
                if any(task.done() for task in pending):
                    await asyncio.gather(*(t for t in pending if t.done()))
                assert time.monotonic() < end, 'Waits did not reach the service'
                await asyncio.sleep(.01)
            async with session(listener) as client:
                assert await asyncio.wait_for(call(client, 'get_rules', game_id='dice_circuit'), 2)
                response = await client.call_tool('wait_for_decision', {'table_id': tid, 'mode': 'changed', 'timeout': 0})
                assert response.is_error and response.structured_content['code'] == 'busy'
                # This private move cannot satisfy any observer's changed wait.
                assert await call(client, 'apply_action', table_id=tid, credential=tokens[0], decision_id='choice-0', action_id='choose')
                assert host.service._wait_hub.count == 32
                assert all(not task.done() for task in pending)
        finally:
            for task in pending: task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        end = time.monotonic() + 3
        while host.service._wait_hub.count:
            assert time.monotonic() < end, 'Disconnected waiters retained resources'
            await asyncio.sleep(.01)
        async with session(listener) as client:
            assert (await call(client, 'wait_for_decision', table_id=tid, mode='changed', timeout=0))['reason'] == 'timeout'
    try:
        asyncio.run(run_test())
    finally:
        if relay: listener.close()


def test_rest_accepts_tokens_and_idempotency_for_every_mutation(host):
    config = dict(game_id='mcp2_synthetic', variant='base', players=2)
    body = dict(config=config, seats=['human'] * 2, idempotency_key=KEY)
    status, made = rest(host, 'POST', '/api/tables', body)
    assert status == 200
    assert rest(host, 'POST', '/api/tables', body)[1] == made
    path = '/api/tables/' + made['table_id']
    tokens = []
    for seat in range(2):
        body = dict(seat=seat, idempotency_key=KEY + str(seat))
        status, joined = rest(host, 'POST', path + '/join', body)
        assert status == 200
        assert rest(host, 'POST', path + '/join', body)[1] == joined
        tokens.append(joined['credential'])
    body = {'idempotency_key': KEY}
    started = rest(host, 'POST', path + '/start', body, made['creator_credential'])
    assert started[0] == 200
    assert rest(host, 'POST', path + '/start', body, made['creator_credential']) == started
    first = rest(host, 'GET', path + '/view', token=tokens[0])[1]
    body = dict(decision_id='choice-0', action_id='choose', decision_token=first['decision_token'], idempotency_key=KEY)
    applied = rest(host, 'POST', path + '/actions', body, tokens[0])
    assert applied[0] == 200 and applied[1]['contract_version'] == '1'
    assert rest(host, 'POST', path + '/actions', body, tokens[0]) == applied
    second = rest(host, 'GET', path + '/view', token=tokens[1])[1]
    body = dict(decision_id='choice-1', kind='choose', params={}, decision_token=second['decision_token'], idempotency_key=KEY)
    result = rest(host, 'POST', path + '/commands', body, tokens[1])
    assert result[0] == 200 and result[1]['status'] == 'completed'
    assert rest(host, 'POST', path + '/commands', body, tokens[1]) == result


def test_relay_wait_uses_timeout_larger_than_wait_limit(host):
    assert HttpPlayService(host.url).timeout > 25


def test_relay_wait_reaches_rest_directly(host):
    made = host.service.create_game(fixture.GameConfig('mcp2_synthetic', 'base', 2), ['human'] * 2)
    async def run():
        result = await HttpPlayService(host.url).wait_for_decision(made['table_id'], mode='changed', timeout=0)
        assert result['reason'] == 'timeout'
    asyncio.run(run())


def test_relay_explicit_short_timeout_cannot_expire_before_wait(host):
    made = host.service.create_game(fixture.GameConfig('mcp2_synthetic', 'base', 2), ['human'] * 2)
    async def run():
        start = time.monotonic()
        result = await HttpPlayService(host.url, timeout=.01).wait_for_decision(made['table_id'], mode='changed', timeout=.15)
        assert result['reason'] == 'timeout'
        assert time.monotonic() - start >= .15
    asyncio.run(run())


def test_wait_errors_keep_structured_diagnostics_through_rest_and_relay(host):
    status, error = rest(host, 'POST', '/api/tables/missing/wait', {'mode': 'changed', 'timeout': 0})
    assert status == 400 and error['structuredContent']['code'] == 'unknown_table'
    made = host.service.create_game(fixture.GameConfig('mcp2_synthetic', 'base', 2), ['human'] * 2)
    async def run():
        from boardcraft.server.errors import ServiceError
        with pytest.raises(ServiceError) as failure:
            await HttpPlayService(host.url).wait_for_decision(made['table_id'], 'wrong-secret', timeout=0)
        assert failure.value.code == 'invalid_credential'
        assert 'wrong-secret' not in str(failure.value)
        assert host.service._wait_hub.count == 0
    asyncio.run(run())
