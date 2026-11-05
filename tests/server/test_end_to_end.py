"""Installed CLI, browser resources, durable HTTP and real MCP stdio integration."""
import asyncio
from mcp2_snapshot import snapshot
from contextlib import contextmanager
import json
import re
import socket
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urljoin, urlsplit

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters


def assert_public(value):
    if isinstance(value, dict):
        assert not set(value) & {'seed', 'rng', 'rng_state', 'snapshot', 'creator_hash', 'hash', 'steps'}
        for child in value.values():
            assert_public(child)
    elif isinstance(value, list):
        for child in value:
            assert_public(child)


class Http:
    def __init__(self, base):
        self.base = base

    def read(self, path, data=None, token=None, status=200, raw=False):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        request = Request(self.base + path, data=None if data is None else json.dumps(data).encode(), headers=headers)
        try:
            response = urlopen(request, timeout=20)
        except HTTPError as error:
            response = error
        with response:
            body = response.read()
            assert response.status == status, (path, response.status, body)
            if raw:
                return body
            value = json.loads(body)
            assert_public(value)
            return value


@contextmanager
def serve(runtime, data, *, model_source=None):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    process = subprocess.Popen([runtime['python'], '-I', '-B', '-m', 'boardcraft', 'serve',
        '--host', '127.0.0.1', '--port', str(port), '--data-dir', str(data),
        *([] if model_source is None else ['--model-source', str(model_source)])],
        cwd=runtime['cwd'], env=runtime['env'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    http = Http(f'http://127.0.0.1:{port}')
    try:
        deadline = time.monotonic() + 25
        while True:
            try:
                http.read('/api/games')
                break
            except URLError:
                assert process.poll() is None, 'Installed server exited during startup'
                assert time.monotonic() < deadline, 'Installed server startup timed out'
                time.sleep(.025)
        yield http
    finally:
        if process.poll() is None:
            process.terminate()
        stdout, stderr = process.communicate(timeout=15)
        assert b'Traceback' not in stdout + stderr, (stdout + stderr).decode(errors='replace')


def test_installed_model_catalog_shared_http_stdio_restart_and_missing_source(installed_runtime):
    from tests.inference.test_catalog import artifact
    runtime = installed_runtime
    paths = artifact(runtime['root'] / 'synthetic-policy')
    manifest = runtime['root'] / 'synthetic-sources.json'
    manifest.write_text(json.dumps({'version': 1, 'models': [{'choice_id': 'fixture', 'metadata': str(paths[0]), 'weights': str(paths[1])}]}))
    data = runtime['root'] / 'model-shared-data'
    # Do not start: pinning must survive both a fresh process and alias rebinding.
    with serve(runtime, data, model_source=manifest) as http:
        advertised = http.read('/api/games/dice_circuit/bots')
        pinned = advertised[-1]['reference']
        made = http.read('/api/tables', {'config': {'game_id': 'dice_circuit', 'variant': 'base', 'players': 1}, 'seats': ['fixture']})
        table = made['table_id']
        assert made['seats'][0]['bot_ref'] == pinned
        async def inspect():
            parameters = StdioServerParameters(command=runtime['python'], args=['-I', '-B', '-m', 'boardcraft', 'mcp', '--data-dir', str(data), '--model-source', str(manifest)], cwd=runtime['cwd'], env=runtime['env'])
            async with Client(parameters, read_timeout_seconds=30) as client:
                discovered = await client.call_tool('list_bots', {'game_id': 'dice_circuit'})
                assert not discovered.is_error
                assert json.loads(discovered.content[0].text) == advertised
                view = await client.call_tool('observe_game', {'table_id': table})
                assert json.loads(view.content[0].text)['seats'][0]['bot_ref'] == pinned
        asyncio.run(inspect())
    replacement = artifact(runtime['root'] / 'replacement-policy', 1, 'Second synthetic policy')
    manifest.write_text(json.dumps({'version': 1, 'models': [
        {'choice_id': 'fixture', 'metadata': str(replacement[0]), 'weights': str(replacement[1])},
        {'choice_id': 'retained-fixture', 'metadata': str(paths[0]), 'weights': str(paths[1])}]}))

    def wait_for_availability(http, expected):
        deadline = time.monotonic() + 25
        while True:
            view = http.read(f'/api/tables/{table}/view')
            assert view['status'] == 'lobby'
            seat = view['seats'][0]
            assert seat['bot_ref'] == pinned
            availability = seat['availability']
            if availability == {'reference': pinned, 'status': expected}:
                return
            assert time.monotonic() < deadline, (expected, availability)

    with serve(runtime, data) as http:
        assert len(http.read('/api/games/dice_circuit/bots')) == 3
        wait_for_availability(http, 'unavailable')
        http.read(f'/api/tables/{table}/start', {}, made['creator_credential'], status=400)
    with serve(runtime, data, model_source=manifest) as http:
        wait_for_availability(http, 'available')
        assert next(item for item in http.read('/api/games/dice_circuit/bots') if item['choice_id'] == 'fixture')['reference'] != pinned
        http.read(f'/api/tables/{table}/start', {}, made['creator_credential'])
        deadline = time.monotonic() + 60
        while http.read(f'/api/tables/{table}/result')['result'] is None:
            assert time.monotonic() < deadline, 'Synthetic policy did not complete ordinary play'
            time.sleep(.05)
        expected = http.read(f'/api/tables/{table}/replay?position=0')
    with serve(runtime, data) as http:
        assert http.read(f'/api/tables/{table}/replay?position=0') == expected


def replay_all(http, table, token=None):
    first = http.read(f'/api/tables/{table}/replay?position=0', token=token)
    assert set(first) == {'observation', 'events', 'position', 'total_positions'}
    sequence = []
    for position in range(first['total_positions']):
        frame = first if position == 0 else http.read(f'/api/tables/{table}/replay?position={position}', token=token)
        assert frame['position'] == position
        assert frame['total_positions'] == first['total_positions']
        assert frame['events'], 'Every viewer-local position must publish permitted events'
        sequence.extend(event['sequence'] for event in frame['events'])
    assert sequence == list(range(1, len(sequence) + 1))
    assert frame['observation'] == http.read(f'/api/tables/{table}/view', token=token)['observation']
    http.read(f'/api/tables/{table}/replay?position={first["total_positions"]}', token=token, status=400)
    return first['total_positions']


def test_installed_cli_static_bot_game_restart_and_replay(installed_runtime):
    data = installed_runtime['root'] / 'bot-data'
    with serve(installed_runtime, data) as http:
        html = http.read('/', raw=True).decode()
        assets = re.findall(r'(?:src|href)="([^"]+)"', html)
        assert any(path.endswith('.js') for path in assets)
        assert any(path.endswith('.css') for path in assets)
        for path in assets:
            resource = urlsplit(urljoin(http.base + '/', path))
            assert resource.netloc == urlsplit(http.base).netloc, 'Static resources must be served locally'
            assert http.read(resource.path, raw=True)
        assert http.read('/boardcraft-emblem.png', raw=True).startswith(b'\x89PNG\r\n\x1a\n')
        assert '# Dice Circuit' in http.read('/api/games/dice_circuit/rules', raw=True).decode()
        assert http.read('/api/games/dice_circuit/sheet')['resourceSlots'] == 7
        made = http.read('/api/tables', {'config': {'game_id': 'dice_circuit', 'variant': 'base', 'players': 2},
                                         'seats': ['greedy', 'random']})
        table = made['table_id']
        http.read(f'/api/tables/{table}/start', {}, made['creator_credential'])
        deadline = time.monotonic() + 90
        while True:
            final = http.read(f'/api/tables/{table}/result')
            if final['result'] is not None:
                break
            assert time.monotonic() < deadline, 'Installed bots did not complete the game'
            time.sleep(.05)
        assert len(final['result']['scores']) == 2
        view = http.read(f'/api/tables/{table}/view')
        assert view['status'] == 'completed'
        assert http.read(f'/api/tables/{table}/actions')['actions'] == []
        positions = replay_all(http, table)
    with serve(installed_runtime, data) as http:
        assert snapshot(http.read(f'/api/tables/{table}/view')) == snapshot(view)
        assert http.read(f'/api/tables/{table}/result') == final
        assert replay_all(http, table) == positions
    print(f'Installed bots completed; durable observer replay positions: {positions}; result: {final}')


def test_installed_mcp_agent_and_http_share_game(installed_runtime):
    runtime = installed_runtime
    data = runtime['root'] / 'agent-data'
    with serve(runtime, data) as http:
        async def run():
            parameters = StdioServerParameters(command=runtime['python'],
                args=['-I', '-B', '-m', 'boardcraft', 'mcp', '--data-dir', str(data)],
                cwd=runtime['cwd'], env=runtime['env'])
            async with Client(parameters, read_timeout_seconds=30) as client:
                async def call(name, error=False, **arguments):
                    response = await client.call_tool(name, arguments)
                    assert response.is_error == error, response
                    if error:
                        return
                    value = json.loads(response.content[0].text)
                    assert_public(value)
                    return value
                discovered = await client.list_tools()
                names = {tool.name for tool in discovered.tools}
                assert {'list_games', 'get_rules', 'create_game', 'join_game', 'start_game', 'observe_game',
                        'legal_actions', 'apply_action', 'get_events', 'get_result', 'get_replay'} <= names
                assert await call('list_games') == http.read('/api/games')
                assert await call('get_rules', game_id='dice_circuit') == http.read('/api/games/dice_circuit/rules', raw=True).decode()
                made = await call('create_game', config={'game_id': 'dice_circuit', 'variant': 'base', 'players': 2}, seats=['human', 'human'])
                table, owner = made['table_id'], made['creator_credential']
                joined = await call('join_game', table_id=table, seat=0)
                token = joined['credential']
                second = (await call('join_game', table_id=table, seat=1))['credential']
                assert token != owner
                assert http.read(f'/api/tables/{table}/view')['seats'][0]['occupied']
                await call('start_game', table_id=table, credential=token, error=True)
                http.read(f'/api/tables/{table}/start', {}, token, status=403)
                await call('observe_game', table_id=table, credential=owner, error=True)
                await call('start_game', table_id=table, credential=owner)
                observer = await call('observe_game', table_id=table)
                assert snapshot(observer) == snapshot(http.read(f'/api/tables/{table}/view'))
                assert observer['observation']['viewer'] is None
                assert (await call('legal_actions', table_id=table))['actions'] == []
                http.read(f'/api/tables/{table}/actions', {'decision_id': 'unavailable', 'action_id': 'unavailable'}, status=403)
                frames = {None: [observer['observation']], token: [http.read(f'/api/tables/{table}/view', token=token)['observation']]}
                cursors = {viewer: http.read(f'/api/tables/{table}/events', token=viewer)['next_cursor'] for viewer in frames}
                for step in range(500):
                    observed = await call('observe_game', table_id=table, credential=token)
                    assert snapshot(observed) == snapshot(http.read(f'/api/tables/{table}/view', token=token))
                    final = await call('get_result', table_id=table)
                    assert final == http.read(f'/api/tables/{table}/result')
                    if final['result'] is not None:
                        assert len(final['result']['placements']) == 2
                        assert len(frames[None]) < step + 1, 'The match must exercise hidden-only accepted choices'
                        for viewer, expected in frames.items():
                            positions = replay_all(http, table, viewer)
                            assert positions == len(expected)
                            for position in sorted({0, positions // 2, positions - 1}):
                                replay = await call('get_replay', table_id=table, credential=viewer, position=position)
                                assert replay == http.read(f'/api/tables/{table}/replay?position={position}', token=viewer)
                                assert replay['observation'] == expected[position]
                        print(f'Installed agent completed {step} actions; observer/seat replay positions: {[len(value) for value in frames.values()]}; result: {final}')
                        return
                    for acting in (token, second):
                        choices = await call('legal_actions', table_id=table, credential=acting)
                        assert choices == http.read(f'/api/tables/{table}/actions', token=acting)
                        if choices['actions']:
                            break
                    assert choices['actions'], 'A playing human table must have an eligible seat'
                    action = choices['actions'][0]
                    body = {'decision_id': choices['decision_id'], 'action_id': action['id']}
                    if step % 2:
                        result = await call('apply_action', table_id=table, credential=acting, **body)
                        assert snapshot(result) == snapshot(http.read(f'/api/tables/{table}/view', token=acting))
                    else:
                        result = http.read(f'/api/tables/{table}/actions', body, acting)
                        assert snapshot(result) == snapshot(await call('observe_game', table_id=table, credential=acting))
                    assert await call('get_events', table_id=table, credential=token) == http.read(f'/api/tables/{table}/events', token=token)
                    for viewer in frames:
                        page = http.read(f'/api/tables/{table}/events?cursor={cursors[viewer]}&limit=200', token=viewer)
                        assert len(page['events']) < 200, 'One accepted action must fit the event page'
                        cursors[viewer] = page['next_cursor']
                        if page['events']:
                            frames[viewer].append(http.read(f'/api/tables/{table}/view', token=viewer)['observation'])
                raise AssertionError('Installed agent game did not finish')
        asyncio.run(run())
