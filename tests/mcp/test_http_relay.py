"""Gameplay MCP forwards bounded requests to one configured HTTP instance."""
import asyncio
import io
import json
import socket
from http.client import IncompleteRead
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, parse_qs
import pytest
from mcp2_snapshot import snapshot
from fastapi.testclient import TestClient
from mcp.client import Client
from boardcraft.server.api import create_app
from boardcraft.server.service import PlayService


class Response(io.BytesIO):
    def __init__(self, body=b'{"ok":true}', content_type='application/json', status=200):
        super().__init__(body)
        self.headers = {'Content-Type': content_type}
        self.status = status


class Opener:
    def __init__(self, response=None, error=None):
        self.calls = []
        self.response = response
        self.error = error

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        if self.error:
            raise self.error
        return self.response or Response()


TOURNAMENT = {'name': 'Fixture', 'config': {'game_id': 'dice_circuit', 'variant': 'base', 'players': 1},
              'rules_version': '1', 'engine_version': '1', 'seats': ['random'], 'seeds': [11],
              'sampler_seeds': [[21]], 'rotations': [0], 'match_count': 1, 'schedule_cap': 1,
              'max_inflight': 1, 'max_record_bytes': 1000000, 'max_decisions': 1000}
FIT = {'gauge': 'component_sum_zero', 'elo_origin': 0.0, 'ridge_lambda': 0.0, 'prior_pseudocount': 0.0,
       'solver': 'newton_backtracking', 'max_iterations': 10, 'max_backtracks': 10,
       'gradient_inf_tolerance': 1e-6, 'armijo_coefficient': 0.1, 'backtrack_factor': 0.5}
T = 'a' * 32
M = 'b' * 64
TOKEN = 'seat-secret'
TABLE = {'table_id': T, 'credential': TOKEN}
TOUR = {'tournament_id': T, 'credential': TOKEN, 'revision': 2}

CASES = [
    ('list_games', {}, 'GET', '/api/games', None, None),
    ('list_bots', {'game_id': 'dice_circuit'}, 'GET', '/api/games/dice_circuit/bots', None, None),
    ('get_board', {'game_id': 'dice_circuit'}, 'GET', '/api/games/dice_circuit/board', None, None),
    ('get_settings', {'game_id': 'dice_circuit'}, 'GET', '/api/games/dice_circuit/settings', None, None),
    ('get_rules', {'game_id': 'dice_circuit'}, 'GET', '/api/games/dice_circuit/rules', None, None),
    ('list_tables', {'game_id': 'dice_circuit', 'status': 'lobby', 'offset': 2, 'limit': 3}, 'GET', '/api/tables?game_id=dice_circuit&status=lobby&offset=2&limit=3', None, None),
    ('create_game', {'config': TOURNAMENT['config'], 'seats': ['agent'], 'visibility': 'listed'}, 'POST', '/api/tables', {'config': TOURNAMENT['config'], 'seats': ['agent'], 'live': {}, 'visibility': 'listed'}, None),
    ('join_game', {'table_id': T, 'seat': 0}, 'POST', f'/api/tables/{T}/join', {'seat': 0}, None),
    ('join_game', {'table_id': T, 'seat': 0, 'name': 'Robin'}, 'POST', f'/api/tables/{T}/join', {'seat': 0, 'name': 'Robin'}, None),
    ('create_game', {'config': TOURNAMENT['config'], 'seats': ['agent'], 'seat_names': ['Robin']}, 'POST', '/api/tables', {'config': TOURNAMENT['config'], 'seats': ['agent'], 'live': {}, 'visibility': 'invite', 'seat_names': ['Robin']}, None),
    ('start_game', TABLE, 'POST', f'/api/tables/{T}/start', {}, TOKEN),
    ('observe_game', TABLE, 'GET', f'/api/tables/{T}/view', None, TOKEN),
    ('legal_actions', TABLE, 'GET', f'/api/tables/{T}/actions', None, TOKEN),
    ('apply_action', {**TABLE, 'decision_id': 'decision', 'action_id': 'action'}, 'POST', f'/api/tables/{T}/actions', {'decision_id': 'decision', 'action_id': 'action'}, TOKEN),
    ('submit_command', {**TABLE, 'expected_revision': 1, 'kind': 'resign', 'params': {}}, 'POST', f'/api/tables/{T}/commands', {'expected_revision': 1, 'kind': 'resign', 'params': {}, 'decision_id': None}, TOKEN),
    ('get_events', {**TABLE, 'cursor': 1, 'limit': 2}, 'GET', f'/api/tables/{T}/events?cursor=1&limit=2', None, TOKEN),
    ('get_result', TABLE, 'GET', f'/api/tables/{T}/result', None, TOKEN),
    ('get_replay', {**TABLE, 'position': 3}, 'GET', f'/api/tables/{T}/replay?position=3', None, TOKEN),
    ('tournament_schema', {}, 'GET', '/api/tournaments/schema', None, None),
    ('list_tournaments', {}, 'GET', '/api/tournaments', None, None),
    # The relay sends the validated input: start defaults to False (saved only).
    ('create_tournament', TOURNAMENT, 'POST', '/api/tournaments', {**TOURNAMENT, 'start': False}, None),
    ('get_tournament', {'tournament_id': T}, 'GET', f'/api/tournaments/{T}', None, None),
    ('start_tournament', {**TOUR, 'spec_sha': M}, 'POST', f'/api/tournaments/{T}/start', {'spec_sha': M, 'revision': 2}, TOKEN),
    ('restart_tournament', {**TOUR, 'spec_sha': M}, 'POST', f'/api/tournaments/{T}/restart', {'spec_sha': M, 'revision': 2}, TOKEN),
    ('stop_tournament', TOUR, 'POST', f'/api/tournaments/{T}/stop', {'revision': 2}, TOKEN),
    ('delete_tournament', TOUR, 'POST', f'/api/tournaments/{T}/delete', {'revision': 2}, TOKEN),
    ('fit_tournament_ratings', {**TOUR, 'fit': FIT}, 'POST', f'/api/tournaments/{T}/fit', {'revision': 2, 'fit': FIT}, TOKEN),
    ('get_tournament_ratings', {'tournament_id': T}, 'GET', f'/api/tournaments/{T}/ratings', None, None),
    ('get_tournament_match_replay', {'tournament_id': T, 'match_id': M, 'position': 1}, 'GET', f'/api/tournaments/{T}/matches/{M}/replay?position=1', None, None),
]


@pytest.mark.parametrize('name,args,method,path,body,token', CASES)
def test_every_mcp_tool_uses_exact_http_route(name, args, method, path, body, token):
    from boardcraft.mcp.http import HttpPlayService
    from boardcraft.mcp.server import create_server
    opener = Opener(Response(b'Rules text', 'text/plain; charset=utf-8') if name == 'get_rules' else None)
    service = HttpPlayService('http://example.test:8000/', opener=opener)
    async def run():
        async with Client(create_server(service)) as client:
            result = await client.call_tool(name, args)
            assert not result.is_error, result
            assert json.loads(result.content[0].text) == ('Rules text' if name == 'get_rules' else {'ok': True})
    asyncio.run(run())
    assert len(opener.calls) == 1
    request, timeout = opener.calls[0]
    assert timeout == 30.0
    assert request.full_url == 'http://example.test:8000' + path
    assert request.method == method
    assert (json.loads(request.data) if request.data is not None else None) == body
    assert request.get_header('Authorization') == (None if token is None else 'Bearer ' + token)
    assert TOKEN not in request.full_url and TOKEN not in (request.data or b'').decode()


@pytest.mark.parametrize('url', ['ftp://example.test', 'http://user:pass@example.test', 'http://example.test/api', 'http://example.test?x=1', 'http://example.test#x', '//example.test', 'http://', 'http://example.test:bad', 'http://example.test\n', 'http://example.test\\evil'])
def test_relay_rejects_non_origin_urls(url):
    from boardcraft.mcp.http import HttpPlayService
    with pytest.raises(ValueError):
        HttpPlayService(url)


@pytest.mark.parametrize('status,expected', [(400, 'Game or table unavailable'), (404, 'Game or table unavailable'), (401, 'This game credential'), (403, 'This game credential'), (409, 'The game changed'), (422, 'The game request is invalid'), (429, 'Boardcraft is busy'), (503, 'Boardcraft is busy'), (302, 'The configured server returned'), (500, 'The configured server returned')])
def test_safe_http_errors_and_no_retries(status, expected):
    from boardcraft.mcp.http import HttpPlayService, RelayError
    opener = Opener(error=HTTPError('http://secret.example/' + TOKEN, status, TOKEN, {}, io.BytesIO(TOKEN.encode())))
    service = HttpPlayService('http://example.test', opener=opener)
    with pytest.raises(RelayError) as error:
        service.start_game(T, TOKEN)
    assert str(error.value).startswith(expected)
    assert TOKEN not in str(error.value) and 'secret.example' not in str(error.value)
    assert len(opener.calls) == 1


@pytest.mark.parametrize('failure', [socket.timeout(TOKEN), URLError(TOKEN), ConnectionResetError(TOKEN)])
def test_lost_confirmation_is_safe_and_never_retried(failure):
    from boardcraft.mcp.http import HttpPlayService, RelayError
    opener = Opener(error=failure)
    with pytest.raises(RelayError, match='confirmation was lost'):
        HttpPlayService('http://example.test', opener=opener).start_game(T, TOKEN)
    assert len(opener.calls) == 1


@pytest.mark.parametrize('response', [Response(b'bad'), Response(b'{}', 'text/html'), Response(b'x' * (8 * 1024 * 1024 + 1)), Response(b'{}', status=302), Response(b'{"bad":NaN}'), Response(b'{"bad":1e9999}'), Response(b'[' * 2000 + b']' * 2000)])
def test_invalid_responses_are_bounded_and_redacted(response):
    from boardcraft.mcp.http import HttpPlayService, RelayError
    with pytest.raises(RelayError, match='invalid Boardcraft response'):
        HttpPlayService('https://example.test', opener=Opener(response)).list_games()


def test_request_bounds_encoding_auth_isolation_and_default_redirect_policy():
    from boardcraft.mcp.http import HttpPlayService, RelayError
    opener = Opener()
    service = HttpPlayService('http://example.test', opener=opener)
    service.get_view('a/b?c#d', TOKEN)
    service.list_games()
    assert opener.calls[0][0].full_url == 'http://example.test/api/tables/a%2Fb%3Fc%23d/view'
    assert opener.calls[1][0].get_header('Authorization') is None
    for token in ('secret\nvalue', 'secret\rvalue', 'nonascii-ą'):
        with pytest.raises(RelayError) as error:
            service.get_view(T, token)
        assert token not in str(error.value)
    for body in ({'oversize': 'x' * 65536}, {'nan': float('nan')}):
        with pytest.raises(RelayError):
            service.create_tournament(body)
    assert len(opener.calls) == 2
    service = HttpPlayService('http://example.test')
    from urllib.request import Request, HTTPRedirectHandler
    redirect = next(item for item in service._opener.handlers if isinstance(item, HTTPRedirectHandler))
    assert redirect.redirect_request(Request('http://example.test'), None, 302, 'Found', {}, 'http://other.test') is None


def test_relay_error_has_specific_mcp_message():
    from boardcraft.mcp.http import HttpPlayService
    from boardcraft.mcp.server import create_server
    async def run():
        async with Client(create_server(HttpPlayService('http://example.test', opener=Opener(error=URLError(TOKEN))))) as client:
            result = await client.call_tool('list_games', {})
            assert result.is_error
            assert 'confirmation was lost' in result.content[0].text
            assert TOKEN not in result.content[0].text
    asyncio.run(run())


def test_relay_keeps_structured_replay_constraints_without_remote_message_text():
    from boardcraft.mcp.http import HttpPlayService
    from boardcraft.mcp.server import create_server
    payload = {'isError': True, 'detail': 'E:/private secret=do-not-echo', 'structuredContent': {
        'code': 'out_of_range', 'argument': 'position', 'message': 'E:/private secret=do-not-echo',
        'constraints': {'minimum': 0, 'maximum': 40}}}
    opener = Opener(error=HTTPError('http://testserver', 400, 'Hidden',
                                  {'Content-Type': 'application/json'}, io.BytesIO(json.dumps(payload).encode())))
    async def run():
        async with Client(create_server(HttpPlayService('http://testserver', opener=opener))) as client:
            response = await client.call_tool('get_replay', {'table_id': T, 'position': 41})
            assert response.is_error
            assert response.structured_content['code'] == 'out_of_range'
            assert response.structured_content['argument'] == 'position'
            assert response.structured_content['constraints'] == {'minimum': 0, 'maximum': 40}
            assert '0..40' in response.content[0].text
            assert 'do-not-echo' not in json.dumps(response.model_dump())
    asyncio.run(run())


@pytest.mark.parametrize('structured', [
    {'code': 'E:/secret', 'argument': 'credential', 'message': TOKEN},
    {'code': 'out_of_range', 'argument': TOKEN, 'constraints': {'minimum': 0, 'maximum': 40}},
    {'code': 'out_of_range', 'argument': 'position', 'constraints': {'minimum': 0, 'maximum': TOKEN}},
])
def test_relay_rejects_unknown_or_malformed_error_metadata(structured):
    from boardcraft.mcp.http import HttpPlayService, RelayError
    body = json.dumps({'isError': True, 'structuredContent': structured}).encode()
    opener = Opener(error=HTTPError('http://testserver', 400, 'Hidden',
                                  {'Content-Type': 'application/json'}, io.BytesIO(body)))
    with pytest.raises(RelayError) as error:
        HttpPlayService('http://testserver', opener=opener).get_replay(T, position=41)
    assert error.value.code == 'unavailable'
    assert TOKEN not in str(error.value)


def test_truncated_http_response_cannot_leak_remote_exception():
    from boardcraft.mcp.http import HttpPlayService, RelayError
    class Truncated(Response):
        def read(self, size=-1):
            assert size == 8 * 1024 * 1024 + 1
            raise IncompleteRead(TOKEN.encode(), 200)
    with pytest.raises(RelayError, match='invalid Boardcraft response') as error:
        HttpPlayService('http://example.test', opener=Opener(Truncated())).list_games()
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize('method,payload,media', [
    ('get_rules', b'Choose a die', 'text/plain'),
    ('start_game', b'{"ok":true}', 'application/json'),
])
@pytest.mark.parametrize('framing', ['short-fixed', 'complete-fixed', 'chunked', 'close-delimited'])
def test_real_http_response_framing_is_complete_without_repeating(method, payload, media, framing):
    from boardcraft.mcp.http import HttpPlayService, RelayError
    requests = []
    class Endpoint(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, *args):
            pass
        def serve(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            requests.append((self.command, self.path, self.headers.get('Authorization'), body))
            self.send_response(200)
            self.send_header('Content-Type', media)
            self.send_header('Connection', 'close')
            if framing in ('short-fixed', 'complete-fixed'):
                self.send_header('Content-Length', str(len(payload) + (100 if framing == 'short-fixed' else 0)))
            elif framing == 'chunked':
                self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            if framing == 'chunked':
                self.wfile.write(f'{len(payload):x}\r\n'.encode('ascii') + payload + b'\r\n0\r\n\r\n')
            else:
                self.wfile.write(payload)
        do_GET = serve
        do_POST = serve
    with HTTPServer(('127.0.0.1', 0), Endpoint) as host:
        worker = Thread(target=lambda: host.serve_forever(poll_interval=0.01))
        worker.start()
        try:
            service = HttpPlayService(f'http://127.0.0.1:{host.server_port}', timeout=3)
            def invoke():
                return service.get_rules('dice_circuit') if method == 'get_rules' else service.start_game(T, TOKEN)
            if framing == 'short-fixed':
                with pytest.raises(RelayError) as error:
                    invoke()
                assert str(error.value) == 'The configured server returned an invalid Boardcraft response.'
                assert TOKEN not in str(error.value)
            else:
                assert invoke() == (payload.decode() if method == 'get_rules' else {'ok': True})
        finally:
            host.shutdown()
            worker.join(4)
            assert not worker.is_alive()
    assert requests == [('GET', '/api/games/dice_circuit/rules', None, b'') if method == 'get_rules'
                        else ('POST', f'/api/tables/{T}/start', 'Bearer ' + TOKEN, b'{}')]


def test_bounded_response_utf8_and_request_content_type():
    from boardcraft.mcp.http import HttpPlayService
    class Bounded(Response):
        def read(self, size=-1):
            assert size == 8 * 1024 * 1024 + 1
            return super().read(size)
    opener = Opener(Bounded())
    service = HttpPlayService('http://example.test', opener=opener)
    service.create_tournament({'name': 'Dice ♟'})
    request = opener.calls[0][0]
    assert request.get_header('Content-type') == 'application/json; charset=utf-8'
    assert json.loads(request.data.decode('utf-8')) == {'name': 'Dice ♟'}


class ClientOpener:
    def __init__(self, client):
        self.client = client
        self.paths = []

    def open(self, request, timeout):
        path = urlsplit(request.full_url).path
        self.paths.append(path)
        response = self.client.request(request.method, request.full_url, content=request.data, headers=dict(request.header_items()))
        if response.status_code >= 400:
            raise HTTPError(request.full_url, response.status_code, 'Hidden', {}, None)
        return Response(response.content, response.headers['content-type'], response.status_code)


@pytest.mark.parametrize('game,players', [('dice_circuit', 1), ('rentcraft', 2)])
def test_rest_mcp_relay_share_exact_service_instance(tmp_path, game, players):
    from boardcraft.mcp.http import HttpPlayService
    from boardcraft.mcp.server import create_server
    service = PlayService(tmp_path / 'host', seed_factory=lambda: 19)
    other = PlayService(tmp_path / 'other')
    with TestClient(create_app(service, run_bots=False)) as http:
        made = http.post('/api/tables', json={'config': {'game_id': game, 'variant': 'base', 'players': players}, 'seats': ['agent'] * players, 'visibility': 'listed'}).json()
        opener = ClientOpener(http)
        async def run():
            async with Client(create_server(HttpPlayService('http://testserver', opener=opener))) as client:
                async def call(name, **args):
                    result = await client.call_tool(name, args)
                    assert not result.is_error, result
                    return json.loads(result.content[0].text)
                assert (await call('list_tables')) == http.get('/api/tables').json()
                tokens = [(await call('join_game', table_id=made['table_id'], seat=i))['credential'] for i in range(players)]
                path = '/api/tables/' + made['table_id']
                assert http.post(path + '/start', headers={'Authorization': 'Bearer ' + made['creator_credential']}).status_code == 200
                moves = [await call('legal_actions', table_id=made['table_id'], credential=token) for token in tokens]
                actor = next(i for i, move in enumerate(moves) if move['actions'])
                move = moves[actor]
                await call('apply_action', table_id=made['table_id'], credential=tokens[actor], decision_id=move['decision_id'], action_id=move['actions'][0]['id'])
                assert snapshot(await call('observe_game', table_id=made['table_id'])) == snapshot(http.get(path + '/view').json())
                if game == 'rentcraft':
                    move = await call('legal_actions', table_id=made['table_id'], credential=tokens[0])
                    command = next(c for c in move['commands'] if c['kind'] == 'resign')
                    await call('submit_command', table_id=made['table_id'], credential=tokens[0], expected_revision=move['expected_revision'], kind=command['kind'], params={})
                wrong = await client.call_tool('observe_game', {'table_id': '0' * 32})
                assert wrong.is_error and 'Check the configured server' in wrong.content[0].text
        asyncio.run(run())
        assert all(path.startswith('/api/') and not path.startswith('/api/training') for path in opener.paths)
        assert other.list_tables()['total'] == 0


def test_stdio_relay_has_no_local_worker_or_tournament_close(monkeypatch):
    from contextlib import asynccontextmanager
    import boardcraft.mcp.server as adapter
    class Service:
        def close_tournaments(self):
            pytest.fail('Relay closed a local tournament controller')
    class Server:
        def create_initialization_options(self):
            return None
        async def run(self, *args):
            pass
    @asynccontextmanager
    async def stream():
        yield None, None
    monkeypatch.setattr(adapter, 'create_server', lambda service: Server())
    monkeypatch.setattr(adapter, 'stdio_server', stream)
    asyncio.run(adapter.run_stdio(Service(), run_bots=False))


def test_relay_uses_host_catalog_boundary_without_loading_a_provider(tmp_path):
    from dataclasses import replace
    from boardcraft.inference.catalog import RuntimeBotCatalog
    from boardcraft.mcp.http import HttpPlayService
    from boardcraft.mcp.server import create_server
    base = RuntimeBotCatalog()
    class CatalogBoundary:
        available = True
        def list_bots(self, game_id):
            if not self.available:
                raise ValueError('Hidden provider failure')
            return [replace(item, display_name='Host catalog fixture') for item in base.list_bots(game_id)]
    catalog = CatalogBoundary()
    service = PlayService(tmp_path, bot_catalog=catalog)
    with TestClient(create_app(service, run_bots=False)) as http:
        async def run():
            async with Client(create_server(HttpPlayService('http://testserver', opener=ClientOpener(http)))) as client:
                result = await client.call_tool('list_bots', {'game_id': 'dice_circuit'})
                assert not result.is_error
                items = json.loads(result.content[0].text)
                assert all(item['display_name'] == 'Host catalog fixture' for item in items)
                assert items == http.get('/api/games/dice_circuit/bots').json()
                catalog.available = False
                result = await client.call_tool('list_bots', {'game_id': 'dice_circuit'})
                assert result.is_error
                assert 'Check the configured server' in result.content[0].text
                assert 'Hidden provider failure' not in result.content[0].text
        asyncio.run(run())
