"""Official MCP tools share the application service."""
import importlib.util
import pytest
import asyncio
import json


def test_mcp_adapter_exists():
    assert importlib.util.find_spec('boardcraft.mcp') is not None, 'MCP play adapter is missing'


def test_official_inprocess_mcp_agent_workflow(tmp_path):
    assert importlib.util.find_spec('boardcraft.mcp') is not None, 'MCP play adapter is missing'
    from boardcraft.mcp.server import create_server
    from boardcraft.server.service import PlayService
    from mcp.client import Client
    service = PlayService(tmp_path, seed_factory=lambda: 3)
    async def run():
        async with Client(create_server(service)) as client:
            tools = await client.list_tools()
            assert {'list_games', 'get_rules', 'create_game', 'join_game', 'start_game', 'observe_game', 'legal_actions', 'apply_action', 'get_events', 'get_result'} <= {tool.name for tool in tools.tools}
            async def call(name, **args):
                response = await client.call_tool(name, args)
                assert not response.is_error, response
                payload = json.loads(response.content[0].text)
                assert response.structured_content == (payload if isinstance(payload, dict) else {'result': payload})
                return payload
            assert (await call('list_games'))[0]['name'] == 'Dice Circuit'
            assert await call('get_rules', game_id='dice_circuit') == service.get_rules('dice_circuit')
            made = await call('create_game', config={'game_id': 'dice_circuit', 'variant': 'base', 'players': 1}, seats=['human'])
            table = made['table_id']
            joined = await call('join_game', table_id=table, seat=0)
            await call('start_game', table_id=table, credential=made['creator_credential'])
            for _ in range(400):
                result = await call('get_result', table_id=table)
                if result['result'] is not None:
                    assert result['result']['placements'] == [1]
                    return
                view = await call('observe_game', table_id=table, credential=joined['credential'])
                choices = await call('legal_actions', table_id=table, credential=joined['credential'])
                assert view['observation']['decision']['id'] == choices['decision_id']
                await call('apply_action', table_id=table, credential=joined['credential'], decision_id=choices['decision_id'], action_id=choices['actions'][0]['id'])
            pytest.fail('MCP workflow did not finish')
    asyncio.run(run())


def test_mcp_rejects_excess_requests_without_an_unbounded_worker_queue(tmp_path):
    import threading
    from boardcraft.mcp.server import create_server
    from boardcraft.server.service import PlayService
    from mcp.client import Client
    service = PlayService(tmp_path)
    entered, release = threading.Event(), threading.Event()
    lock, count = threading.Lock(), [0]
    original = service.get_rules
    def rules(game_id):
        with lock:
            count[0] += 1
            if count[0] == 16:
                entered.set()
        assert release.wait(10)
        return original(game_id)
    service.get_rules = rules
    async def run():
        async with Client(create_server(service)) as client:
            pending = [asyncio.create_task(client.call_tool('get_rules', {'game_id': 'dice_circuit'})) for _ in range(16)]
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                response = await asyncio.wait_for(client.call_tool('get_rules', {'game_id': 'dice_circuit'}), 5)
                assert response.is_error
                assert response.structured_content['code'] == 'busy'
                assert response.structured_content['message'] == response.content[0].text
            finally:
                release.set()
                await asyncio.gather(*pending)
    asyncio.run(run())


def test_rentcraft_http_mcp_trade_and_discovery(tmp_path):
    from fastapi.testclient import TestClient
    from mcp.client import Client
    from boardcraft.mcp.server import create_server
    from boardcraft.server.api import create_app
    from boardcraft.server.service import PlayService

    service = PlayService(tmp_path, seed_factory=lambda: 19)
    with TestClient(create_app(service, run_bots=False)) as http:
        schema = http.get('/api/games/rentcraft/settings')
        assert schema.status_code == 200
        assert set(schema.json()) == {'table', 'simulation_defaults', 'live', 'bots'}
        assert schema.json()['simulation_defaults'] == {'max_rounds': 200}
        assert len(schema.json()['bots']) == 35
        board = http.get('/api/games/rentcraft/board').json()
        assert len(board['spaces']) == 40
        config = {'game_id': 'rentcraft', 'variant': 'base', 'players': 2, 'settings': {'start_cash': 1234}}
        made = http.post('/api/tables', json={'config': config, 'seats': ['human'] * 2}).json()
        path = '/api/tables/' + made['table_id']
        tokens = [http.post(path + '/join', json={'seat': seat}).json()['credential'] for seat in range(2)]
        def headers(token):
            return {'Authorization': 'Bearer ' + token}
        view = http.post(path + '/start', headers=headers(made['creator_credential'])).json()
        actor = view['observation']['data']['acting_seat']
        target = 1 - actor
        turn = http.get(path + '/actions', headers=headers(tokens[actor])).json()
        body = {'expected_revision': turn['expected_revision'], 'kind': 'propose_trade', 'params': {},
                'decision_id': turn['decision_id']}
        for invalid in ({'proposer': target}, {'target': True}, {'give_spaces': [1, 1]}, {'signature': 'outside'}):
            bad = {**body, 'params': {'target': target, 'give_cash': 5, **invalid}}
            assert http.post(path + '/commands', json=bad, headers=headers(tokens[actor])).status_code >= 400
        body['params'] = {'target': target, 'give_cash': 5}
        assert http.post(path + '/commands', json=body, headers=headers(tokens[actor])).status_code == 200
        assert http.post(path + '/commands', json=body, headers=headers(tokens[actor])).status_code == 409

        async def run():
            async with Client(create_server(service)) as client:
                async def call(name, **args):
                    response = await client.call_tool(name, args)
                    assert not response.is_error, response
                    return json.loads(response.content[0].text)
                assert await call('get_board', game_id='rentcraft') == board
                assert await call('get_settings', game_id='rentcraft') == schema.json()
                seat = {'table_id': made['table_id'], 'credential': tokens[target]}
                offer = await call('legal_actions', **seat)
                await call('submit_command', **seat, decision_id=offer['decision_id'],
                           expected_revision=offer['expected_revision'], kind='counter_trade', params={'give_cash': 4})
                seat['credential'] = tokens[actor]
                offer = await call('legal_actions', **seat)
                await call('apply_action', **seat, decision_id=offer['decision_id'], action_id='accept_trade')
        asyncio.run(run())
        events = [event['kind'] for event in http.get(path + '/events').json()['events']]
        assert [event for event in events if event in ('trade_offer', 'trade_counter', 'trade')] == [
            'trade_offer', 'trade_counter', 'trade']
