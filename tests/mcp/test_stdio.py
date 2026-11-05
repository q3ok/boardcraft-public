"""Real stdio and HTTP processes operate on one durable match."""
import asyncio
from mcp2_snapshot import snapshot
import json
import os
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters


def test_stdio_and_http_share_store_and_complete_one_game(tmp_path):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    command = [sys.executable, '-B', '-m', 'boardcraft', 'serve', '--host', '127.0.0.1', '--port', str(port), '--data-dir', str(tmp_path)]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    base = f'http://127.0.0.1:{port}'
    def http(path, data=None, token=None):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        request = Request(base + path, data=None if data is None else json.dumps(data).encode(), headers=headers)
        with urlopen(request, timeout=10) as response:
            content = response.read().decode()
            return content if path.endswith('/rules') else json.loads(content)
    credentials = []
    try:
        deadline = time.monotonic() + 15
        while True:
            try:
                http('/api/games')
                break
            except URLError:
                assert process.poll() is None, 'HTTP process exited during startup'
                assert time.monotonic() < deadline, 'HTTP startup timed out'
                time.sleep(.025)
        async def run():
            parameters = StdioServerParameters(command=sys.executable, args=['-B', '-m', 'boardcraft', 'mcp', '--data-dir', str(tmp_path)], env=env)
            async with Client(parameters, read_timeout_seconds=10) as client:
                async def call(name, **args):
                    response = await client.call_tool(name, args)
                    assert not response.is_error, response
                    payload = json.loads(response.content[0].text)
                    assert response.structured_content == (payload if isinstance(payload, dict) else {'result': payload})
                    return json.loads(response.content[0].text)
                assert await call('list_games') == http('/api/games')
                assert await call('get_rules', game_id='dice_circuit') == http('/api/games/dice_circuit/rules')
                made = http('/api/tables', {'config': {'game_id': 'dice_circuit', 'variant': 'base', 'players': 1}, 'seats': ['human']})
                table = made['table_id']
                credentials.append(made['creator_credential'])
                joined = await call('join_game', table_id=table, seat=0)
                token = joined['credential']
                credentials.append(token)
                assert http(f'/api/tables/{table}/view')['seats'][0]['occupied'] is True
                http(f'/api/tables/{table}/start', {}, made['creator_credential'])
                for step in range(400):
                    observed = await call('observe_game', table_id=table, credential=token)
                    assert snapshot(observed) == snapshot(http(f'/api/tables/{table}/view', token=token))
                    final = http(f'/api/tables/{table}/result')
                    assert final == await call('get_result', table_id=table)
                    if final['result'] is not None:
                        assert final['result']['placements'] == [1]
                        replay = await call('get_replay', table_id=table, credential=token, position=0)
                        assert set(replay) == {'observation', 'events', 'position', 'total_positions'}
                        assert replay == http(f'/api/tables/{table}/replay?position=0', token=token)
                        last = replay['total_positions'] - 1
                        final_replay = await call('get_replay', table_id=table, credential=token, position=last)
                        assert final_replay['observation'] == observed['observation']
                        assert final_replay == http(f'/api/tables/{table}/replay?position={last}', token=token)
                        failure = await client.call_tool('get_replay', {'table_id': table, 'credential': token, 'position': last + 1})
                        assert failure.is_error
                        assert failure.structured_content['code'] == 'out_of_range'
                        assert failure.structured_content['argument'] == 'position'
                        assert failure.structured_content['constraints'] == {'minimum': 0, 'maximum': last}
                        assert failure.content[0].text == failure.structured_content['message']
                        return
                    choices = await call('legal_actions', table_id=table, credential=token)
                    body = {'decision_id': choices['decision_id'], 'action_id': choices['actions'][0]['id']}
                    if step % 2:
                        await call('apply_action', table_id=table, credential=token, **body)
                    else:
                        http(f'/api/tables/{table}/actions', body, token)
                    assert await call('get_events', table_id=table, credential=token) == http(f'/api/tables/{table}/events', token=token)
                raise AssertionError('Shared-process game did not finish')
        asyncio.run(run())
    finally:
        process.terminate()
        stdout, stderr = process.communicate(timeout=10)
        assert all(token.encode() not in stdout + stderr for token in credentials)
