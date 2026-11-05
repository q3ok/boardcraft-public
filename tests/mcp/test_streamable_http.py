"""Native MCP Streamable HTTP is served by the ordinary gameplay host at /mcp."""
import asyncio
from mcp2_snapshot import snapshot
from contextlib import asynccontextmanager
import http.client
import json
import socket
import sys
import threading
import time
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from boardcraft.registry import list_games
from boardcraft.server.api import create_app
from boardcraft.server.service import PlayService

GAMES = [info for info in list_games()]
LIST_TOOLS = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}})
MARKER = 'leak-marker-9f3a'


class Listener:
    """One real loopback HTTP server on an ephemeral port, shut down by the fixture."""

    def __init__(self, app):
        self.socket = socket.socket()
        self.socket.bind(('127.0.0.1', 0))
        self.port = self.socket.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(app, log_level='warning', access_log=False))
        self.thread = threading.Thread(target=self.server.run, kwargs={'sockets': [self.socket]}, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 15
        while not self.server.started:
            assert self.thread.is_alive(), 'Listener exited during startup'
            assert time.monotonic() < deadline, 'Listener startup timed out'
            time.sleep(.01)

    @property
    def url(self):
        return f'http://127.0.0.1:{self.port}'

    def close(self):
        self.server.should_exit = True
        self.thread.join(15)
        self.socket.close()
        assert not self.thread.is_alive(), 'Listener did not shut down'


@pytest.fixture
def serve(tmp_path):
    listeners = []

    def start(service=None, **options):
        static = tmp_path / 'static'
        static.mkdir(exist_ok=True)
        (static / 'index.html').write_text('<title>Boardcraft UI</title>', encoding='utf-8')
        service = service or PlayService(tmp_path / 'data')
        options.setdefault('run_bots', False)
        listener = Listener(create_app(service, static_dir=static, **options))
        listener.service = service
        listeners.append(listener)
        return listener
    yield start
    for listener in listeners:
        listener.close()


def raw(listener, method='POST', path='/mcp', body=LIST_TOOLS, **headers):
    sent = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}
    sent.update(headers)
    connection = http.client.HTTPConnection('127.0.0.1', listener.port, timeout=10)
    try:
        connection.request(method, path, body=body, headers={key: value for key, value in sent.items() if value is not None})
        response = connection.getresponse()
        return response.status, response.read().decode('utf-8', 'replace')
    finally:
        connection.close()


def rest(listener, method, path, data=None, token=None):
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    status, text = raw(listener, method, path, None if data is None else json.dumps(data), **headers)
    return status, json.loads(text) if text.startswith(('{', '[')) else text


@asynccontextmanager
async def session(listener):
    async with streamable_http_client(listener.url + '/mcp') as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=10) as client:
            await client.initialize()
            yield client


async def attempt(client, name, **arguments):
    response = await client.call_tool(name, arguments)
    text = response.content[0].text
    if not response.is_error:
        payload = json.loads(text)
        assert response.structured_content == (payload if isinstance(payload, dict) else {'result': payload})
    else:
        assert isinstance(response.structured_content['code'], str)
        assert response.structured_content['message'] == text
    return response.is_error, text if response.is_error else json.loads(text)


async def call(client, name, **arguments):
    failed, result = await attempt(client, name, **arguments)
    assert not failed, result
    return result


def test_native_client_uses_the_same_host_that_serves_the_ui(serve):
    listener = serve()

    async def run():
        async with session(listener) as client:
            tools = {tool.name for tool in (await client.list_tools()).tools}
            assert {'list_games', 'create_game', 'apply_action', 'get_replay', 'create_tournament', 'list_rankings'} <= tools
            assert not any(word in name for name in tools for word in ('train', 'job', 'model', 'checkpoint'))
            assert [item['game_id'] for item in await call(client, 'list_games')] == [info.game_id for info in GAMES]
    asyncio.run(run())
    assert raw(listener, 'GET', '/', None, **{'Content-Type': None})[1] == '<title>Boardcraft UI</title>'
    assert rest(listener, 'GET', '/api/games')[0] == 200


def test_endpoint_is_exact_and_rejects_unsupported_transport_requests(serve):
    listener = serve()
    status, body = raw(listener)
    assert status == 200 and json.loads(body)['result']['tools']
    # No idle server-push stream is offered: it would hold a request slot and block shutdown.
    for method in ('GET', 'DELETE', 'PUT'):
        assert raw(listener, method, body=None)[0] == 405
    assert raw(listener, **{'Content-Type': 'text/plain'})[0] == 400
    assert raw(listener, **{'Content-Type': None})[0] == 400
    status, body = raw(listener, body='{"secret": "' + MARKER)
    assert status == 400 and MARKER not in body
    assert raw(listener, 'POST', '/mcp/x')[0] in (404, 405)


def test_event_stream_request_is_refused_and_shutdown_stays_prompt(serve):
    listener = serve()
    with socket.create_connection(('127.0.0.1', listener.port), timeout=10) as stream:
        stream.sendall(f'GET /mcp HTTP/1.1\r\nHost: 127.0.0.1:{listener.port}\r\nAccept: text/event-stream\r\n\r\n'.encode())
        assert stream.recv(12) == b'HTTP/1.1 405'
        listener.close()


def test_body_bounds_and_host_origin_protection(serve):
    listener = serve()
    exact = LIST_TOOLS + ' ' * (65536 - len(LIST_TOOLS))
    assert raw(listener, body=exact)[0] == 200
    assert raw(listener, body=exact + ' ')[0] == 413
    assert raw(listener, **{'Host': 'rebind.example'})[0] == 421
    assert raw(listener, **{'Host': 'localhost:1'})[0] == 200
    assert raw(listener, **{'Origin': 'https://evil.example'})[0] == 403
    assert raw(listener, **{'Origin': 'null'})[0] == 403
    assert raw(listener, **{'Origin': listener.url})[0] == 200
    assert raw(listener)[0] == 200


def test_http_saturation_is_busy_without_affecting_mcp_afterwards(serve):
    listener = serve()
    stalled = []
    try:
        for _ in range(64):
            stalled_socket = socket.create_connection(('127.0.0.1', listener.port), timeout=10)
            stalled_socket.sendall(b'POST /mcp HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nContent-Length: 500\r\n\r\n{')
            stalled.append(stalled_socket)
        deadline = time.monotonic() + 10
        while raw(listener)[0] != 503:
            assert time.monotonic() < deadline, 'Saturated host never answered busy'
            time.sleep(.02)
    finally:
        for stalled_socket in stalled:
            stalled_socket.close()
    deadline = time.monotonic() + 10
    while raw(listener)[0] != 200:
        assert time.monotonic() < deadline, 'Host stayed busy after stalled requests closed'
        time.sleep(.02)


class GatedService(PlayService):
    """Holds list_games calls on worker threads until released."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.gate = threading.Event()
        self.entered = 0
        self.guard = threading.Lock()

    def list_games(self):
        with self.guard:
            self.entered += 1
        assert self.gate.wait(20)
        return super().list_games()


def test_sixteen_call_cap_does_not_leak_on_invalid_or_cancelled_calls(serve, tmp_path):
    service = GatedService(tmp_path / 'gated')
    listener = serve(service)

    async def saturate(client_count=16):
        service.gate.clear()
        service.entered = 0

        async def blocked():
            async with session(listener) as client:
                return await attempt(client, 'list_games')
        tasks = [asyncio.create_task(blocked()) for _ in range(client_count)]
        deadline = time.monotonic() + 20
        while service.entered < 16:
            assert time.monotonic() < deadline, 'Calls never reached the service'
            await asyncio.sleep(.02)
        async with session(listener) as client:
            failed, text = await attempt(client, 'get_rules', game_id='dice_circuit')
            assert failed and 'busy' in text
        service.gate.set()
        return await asyncio.gather(*tasks)

    async def run():
        service.gate.set()
        async with session(listener) as client:
            for _ in range(40):
                failed, text = await attempt(client, 'no_such_tool')
                assert failed and MARKER not in text
                failed, text = await attempt(client, 'get_rules', game_id='dice_circuit', credential=MARKER, extra=MARKER)
                assert failed and MARKER not in text
                failed, text = await attempt(client, 'observe_game', table_id='nope', credential=MARKER)
                assert failed and MARKER not in text
            assert await call(client, 'list_games')
        service.gate.set()
        first = await saturate()
        assert all(not failed for failed, _ in first)
        # A cancelled in-flight call keeps its slot until its worker finishes, then frees it.
        service.gate.clear()
        service.entered = 0

        async def cancelled():
            async with session(listener) as client:
                await attempt(client, 'list_games')
        victim = asyncio.create_task(cancelled())
        while service.entered < 1:
            await asyncio.sleep(.02)
        victim.cancel()
        await asyncio.gather(victim, return_exceptions=True)
        service.gate.set()
        second = await saturate()
        assert all(not failed for failed, _ in second)
    asyncio.run(run())


def table_config(info):
    return {'game_id': info.game_id, 'variant': info.variants[0], 'players': info.min_players}


@pytest.mark.parametrize('info', GAMES, ids=[info.game_id for info in GAMES])
def test_one_table_is_consistent_across_mcp_and_rest(serve, info):
    listener = serve()
    seats = ['human'] * info.min_players

    def denied(failed_mcp, status):
        assert failed_mcp == (status >= 400)

    async def run():
        async with session(listener) as client:
            made = await call(client, 'create_game', config=table_config(info), seats=seats, visibility='listed')
            table = made['table_id']
            assert rest(listener, 'GET', '/api/tables/' + table + '/view')[0] == 200
            tokens = [(await call(client, 'join_game', table_id=table, seat=seat))['credential'] for seat in range(info.min_players)]
            await call(client, 'start_game', table_id=table, credential=made['creator_credential'])
            base = f'/api/tables/{table}'
            for token in [None, *tokens]:
                assert snapshot(await call(client, 'observe_game', table_id=table, credential=token)) == snapshot(rest(listener, 'GET', base + '/view', token=token)[1])
                assert await call(client, 'get_events', table_id=table, credential=token, cursor=0, limit=1) == rest(listener, 'GET', base + '/events?cursor=0&limit=1', token=token)[1]
            assert (await attempt(client, 'get_events', table_id=table, limit=201))[0]
            for bad in ('wrong-credential', None):
                failed, _ = await attempt(client, 'legal_actions', table_id=table, credential=bad)
                denied(failed, rest(listener, 'GET', base + '/actions', token=bad)[0])
            failed, _ = await attempt(client, 'get_replay', table_id=table, credential=tokens[0])
            denied(failed, rest(listener, 'GET', base + '/replay', token=tokens[0])[0])
            assert failed

            async def deciding():
                for token in tokens:
                    choices = await call(client, 'legal_actions', table_id=table, credential=token)
                    assert choices == rest(listener, 'GET', base + '/actions', token=token)[1]
                    if choices['actions']:
                        return token, choices
                raise AssertionError('No seat has a decision')
            token, choices = await deciding()
            step = {'decision_id': choices['decision_id'], 'action_id': choices['actions'][0]['id']}
            assert rest(listener, 'POST', base + '/actions', step, token)[0] == 200
            for each in [None, *tokens]:
                assert await call(client, 'get_events', table_id=table, credential=each, cursor=0, limit=200) == rest(listener, 'GET', base + '/events?cursor=0&limit=200', token=each)[1]
            failed, _ = await attempt(client, 'apply_action', table_id=table, credential=token, **step)
            assert failed and rest(listener, 'POST', base + '/actions', step, token)[0] == 409
            token, choices = await deciding()
            step = {'decision_id': choices['decision_id'], 'action_id': choices['actions'][0]['id']}
            await call(client, 'apply_action', table_id=table, credential=token, **step)
            assert rest(listener, 'POST', base + '/actions', step, token)[0] == 409
            for each in [None, *tokens]:
                assert await call(client, 'get_events', table_id=table, credential=each, cursor=0, limit=200) == rest(listener, 'GET', base + '/events?cursor=0&limit=200', token=each)[1]
        # A fresh MCP connection sees the same persisted table through the same service.
        async with session(listener) as reconnected:
            listed = await call(reconnected, 'list_tables', game_id=info.game_id)
            assert listed == listener.service.list_tables(game_id=info.game_id)
            assert [item['table_id'] for item in listed['items']] == [table]
    asyncio.run(run())


def test_completed_replay_matches_http_for_each_viewer(serve):
    listener = serve()
    info = next(item for item in GAMES if item.min_players == 1)

    async def run():
        async with session(listener) as client:
            made = await call(client, 'create_game', config=table_config(info), seats=['human'])
            table = made['table_id']
            token = (await call(client, 'join_game', table_id=table, seat=0))['credential']
            await call(client, 'start_game', table_id=table, credential=made['creator_credential'])
            for _ in range(500):
                if (await call(client, 'get_result', table_id=table, credential=token))['result']:
                    break
                choices = await call(client, 'legal_actions', table_id=table, credential=token)
                await call(client, 'apply_action', table_id=table, credential=token, decision_id=choices['decision_id'], action_id=choices['actions'][0]['id'])
            else:
                raise AssertionError('Game did not finish')
            for viewer in (token, None):
                for position in (0, 1):
                    expected = rest(listener, 'GET', f'/api/tables/{table}/replay?position={position}', token=viewer)
                    assert expected[0] == 200
                    assert await call(client, 'get_replay', table_id=table, credential=viewer, position=position) == expected[1]
    asyncio.run(run())


def test_lifecycle_has_one_service_one_bot_executor_and_orderly_shutdown(serve, monkeypatch, tmp_path):
    events = []

    class CountingExecutor:
        def __init__(self, service):
            events.append(('new', service))

        def start(self):
            events.append('start')

        def close(self):
            events.append('close')
    monkeypatch.setattr('boardcraft.server.api.BotExecutor', CountingExecutor)
    service = PlayService(tmp_path / 'one')
    listener = serve(service, run_bots=True)
    assert events == [('new', service), 'start']

    async def run():
        async with session(listener) as client:
            made = await call(client, 'create_game', config=table_config(GAMES[0]), seats=['agent'] * GAMES[0].min_players, visibility='listed')
            assert service.get_view(made['table_id'])['table_id'] == made['table_id']
            assert rest(listener, 'GET', '/api/tables')[1]['items'][0]['table_id'] == made['table_id']
    asyncio.run(run())
    assert events == [('new', service), 'start']
    listener.close()
    assert events == [('new', service), 'start', 'close']


def test_missing_mcp_runtime_fails_startup_with_actionable_message(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, 'mcp.server.streamable_http_manager', None)
    monkeypatch.delitem(sys.modules, 'boardcraft.mcp.transport', raising=False)
    with pytest.raises(RuntimeError, match=r'boardcraft\[serve\]'):
        create_app(PlayService(tmp_path), run_bots=False)


def test_serve_extra_declares_mcp_and_keeps_the_separate_stdio_extra():
    from pathlib import Path
    tomllib = pytest.importorskip('tomllib' if sys.version_info >= (3, 11) else 'tomli')
    project = tomllib.loads((Path(__file__).resolve().parents[2] / 'pyproject.toml').read_text(encoding='utf-8'))['project']
    extras = project['optional-dependencies']
    assert extras['mcp'] == ['mcp>=2.3,<3']
    assert 'mcp>=2.3,<3' in extras['serve'] and {'fastapi>=0.162,<1', 'uvicorn>=0.54,<1'} <= set(extras['serve'])
    assert not any(item.startswith('mcp') for item in project['dependencies'])


def test_default_data_dir_order_is_local_app_data_then_xdg_then_home(monkeypatch, tmp_path):
    from boardcraft.server.storage import default_data_dir
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'xdg'))
    assert default_data_dir() == tmp_path / 'local' / 'Boardcraft'
    monkeypatch.delenv('LOCALAPPDATA')
    assert default_data_dir() == tmp_path / 'xdg' / 'Boardcraft'
    monkeypatch.delenv('XDG_DATA_HOME')
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('USERPROFILE', str(tmp_path / 'home'))
    assert default_data_dir() == tmp_path / 'home' / '.local' / 'share' / 'boardcraft'
