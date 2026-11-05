"""Public tool diagnostics retain actionable metadata without reflecting inputs."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from mcp.client import Client

from boardcraft.contracts import GameConfig
from boardcraft.mcp.server import create_server
from boardcraft.server.api import create_app
from boardcraft.server.service import PlayService


def started(tmp_path, players=1):
    service = PlayService(tmp_path, seed_factory=lambda: 9)
    made = service.create_game(GameConfig('dice_circuit', 'base', players), ['human'] * players)
    table = made['table_id']
    tokens = [service.join_game(table, seat)['credential'] for seat in range(players)]
    service.start_game(table, made['creator_credential'])
    return service, made, tokens


def check_error(response, code, argument=None):
    wire = response.model_dump(by_alias=True)
    assert wire['isError'] is True
    data = wire['structuredContent']
    assert data['code'] == code
    assert data.get('argument') == argument
    assert data['message'] and response.content[0].text == data['message']
    return data


def test_tool_errors_identify_the_argument_and_preserve_rest_statuses(tmp_path):
    service, made, tokens = started(tmp_path)
    table, token = made['table_id'], tokens[0]
    turn = service.get_actions(table, token)
    action = {'decision_id': turn['decision_id'], 'action_id': turn['actions'][0]['id']}
    cases = [
        ('observe_game', {'table_id': 'missing'}, 'unknown_table', 'table_id', 'get', '/api/tables/missing/view', {}, 400),
        ('observe_game', {'table_id': table, 'credential': 'wrong-secret'}, 'invalid_credential', 'credential', 'get', f'/api/tables/{table}/view', {'headers': {'Authorization': 'Bearer wrong-secret'}}, 403),
        ('start_game', {'table_id': table}, 'credential_required', 'credential', 'post', f'/api/tables/{table}/start', {}, 403),
        ('apply_action', {'table_id': table, **action}, 'credential_required', 'credential', 'post', f'/api/tables/{table}/actions', {'json': action}, 403),
        ('apply_action', {'table_id': table, 'credential': token, **action, 'decision_id': 'old'}, 'stale_decision', 'decision_id', 'post', f'/api/tables/{table}/actions', {'json': {**action, 'decision_id': 'old'}, 'headers': {'Authorization': 'Bearer ' + token}}, 409),
        ('apply_action', {'table_id': table, 'credential': token, **action, 'action_id': 'illegal'}, 'illegal_action', 'action_id', 'post', f'/api/tables/{table}/actions', {'json': {**action, 'action_id': 'illegal'}, 'headers': {'Authorization': 'Bearer ' + token}}, 409),
    ]
    before = service.get_view(table, token)
    with TestClient(create_app(service, run_bots=False)) as http:
        async def run():
            async with Client(create_server(service)) as client:
                for name, args, code, argument, method, path, kwargs, status in cases:
                    data = check_error(await client.call_tool(name, args), code, argument)
                    rest = getattr(http, method)(path, **kwargs)
                    assert rest.status_code == status
                    assert rest.json()['structuredContent'] == data
                    assert rest.json()['isError'] is True
                    assert rest.json()['detail'] == data['message']
                    assert 'wrong-secret' not in json.dumps(data)
        asyncio.run(run())
    assert {**service.get_view(table, token), 'server_time': before['server_time']} == before


@pytest.mark.parametrize('arguments,argument,constraints', [
    ({'table_id': 'x', 'limit': 201}, 'limit', {'minimum': 1, 'maximum': 200}),
    ({'table_id': 'x', 'position': -1}, 'position', {'minimum': 0}),
    ({'table_id': 'x', 'position': True}, 'position', {'minimum': 0}),
    ({'position': 0}, 'table_id', {'min_length': 1, 'max_length': 128}),
])
def test_invalid_tool_arguments_are_structured_without_echoing_input(tmp_path, arguments, argument, constraints):
    service = PlayService(tmp_path)
    async def run():
        async with Client(create_server(service)) as client:
            tool = 'get_events' if 'limit' in arguments else 'get_replay'
            data = check_error(await client.call_tool(tool, arguments), 'invalid_arguments', argument)
            assert data.get('constraints', {}) == constraints
    asyncio.run(run())


def test_replay_error_has_viewer_local_inclusive_range_in_both_transports(tmp_path):
    service, made, tokens = started(tmp_path, players=3)
    table = made['table_id']
    steps = 0
    while service.get_result(table)['result'] is None:
        for token in tokens:
            turn = service.get_actions(table, token)
            if turn['actions']:
                service.apply_action(table, token, turn['decision_id'], turn['actions'][0]['id'])
                steps += 1
                break
    with TestClient(create_app(service, run_bots=False)) as http:
        async def run():
            async with Client(create_server(service)) as client:
                for token in (None, tokens[0]):
                    total = service.get_replay(table, token)['total_positions']
                    assert total < steps + 1
                    data = check_error(await client.call_tool('get_replay', {'table_id': table, 'credential': token, 'position': total}), 'out_of_range', 'position')
                    assert data['constraints'] == {'minimum': 0, 'maximum': total - 1}
                    assert f'0..{total - 1}' in data['message']
                    headers = {} if token is None else {'Authorization': 'Bearer ' + token}
                    rest = http.get(f'/api/tables/{table}/replay', params={'position': total}, headers=headers)
                    assert rest.status_code == 400
                    assert rest.json()['structuredContent'] == data
                    assert service.get_replay(table, token, total - 1)['position'] == total - 1
        asyncio.run(run())


def test_unexpected_value_errors_do_not_expose_internal_details(tmp_path):
    service = PlayService(tmp_path)
    def broken(_):
        raise ValueError('E:/private/credentials.txt secret=do-not-echo Traceback')
    service.get_rules = broken
    async def run():
        async with Client(create_server(service)) as client:
            check_error(await client.call_tool('get_rules', {'game_id': 'dice_circuit'}), 'invalid_arguments')
    asyncio.run(run())
    with TestClient(create_app(service, run_bots=False)) as http:
        body = http.get('/api/games/dice_circuit/rules').json()
        assert body['isError'] is True
        assert body['structuredContent']['code'] == 'invalid_arguments'
        assert all(part not in json.dumps(body) for part in ('E:/', 'secret', 'Traceback'))


def test_unexpected_tool_failures_and_unknown_operations_are_structured(tmp_path):
    service = PlayService(tmp_path)
    def broken():
        raise RuntimeError('E:/internal/path credential=do-not-echo')
    service.list_games = broken
    async def run():
        async with Client(create_server(service)) as client:
            data = check_error(await client.call_tool('list_games', {}), 'internal_error')
            assert 'do-not-echo' not in json.dumps(data)
            check_error(await client.call_tool('not_a_tool', {}), 'unknown_tool')
    asyncio.run(run())
