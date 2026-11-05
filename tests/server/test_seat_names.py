"""Seat names: the table creator names any seat; a joining player or agent names an unnamed seat itself."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from boardcraft.contracts import GameConfig
from boardcraft.server.service import PlayService
from boardcraft.server.table_admin import set_presentation


def finish(service, table):
    while service.get_result(table)['result'] is None:
        for work in service.bot_work(table):
            service.apply_bot(work, service.choose_bot(work))


def test_the_creator_names_every_kind_of_seat_and_the_names_reach_lists_views_and_replays(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 4)
    made = service.create_game(GameConfig('dice_circuit', 'base', 3), ['human', 'agent', 'greedy'], visibility='listed',
                               seat_names=['Alice', ' Robin ', 'Grumpy bot'])
    table = made['table_id']
    assert [s['display_name'] for s in made['seats']] == ['Alice', 'Robin', 'Grumpy bot']
    assert [s['display_name'] for s in service.list_tables()['items'][0]['seats']] == ['Alice', 'Robin', 'Grumpy bot']
    assert [s['display_name'] for s in service.get_view(table)['seats']] == ['Alice', 'Robin', 'Grumpy bot']
    # A name left out keeps the default name of the seat.
    other = service.create_game(GameConfig('dice_circuit', 'base', 2), ['human', 'greedy'], seat_names=[None, 'Bob'])
    assert [s['display_name'] for s in other['seats']] == ['Human', 'Bob']
    bots = service.create_game(GameConfig('dice_circuit', 'base', 2), ['greedy', 'random'], visibility='listed',
                               seat_names=['Alpha', 'Beta'])
    service.start_game(bots['table_id'], bots['creator_credential'])
    finish(service, bots['table_id'])
    replay = next(r for r in service.list_replays()['items'] if r['table_id'] == bots['table_id'])
    assert [p['display_name'] for p in replay['participants']] == ['Alpha', 'Beta']
    assert [s['display_name'] for s in service.get_view(bots['table_id'])['seats']] == ['Alpha', 'Beta']


@pytest.mark.parametrize('names', [['<script>'], ['x' * 41], [''], ['ok', 'extra'], ['   ']])
def test_invalid_creator_seat_names_are_refused(tmp_path, names):
    service = PlayService(tmp_path)
    with pytest.raises(ValueError):
        service.create_game(GameConfig('dice_circuit', 'base', 1), ['human'], seat_names=names)
    assert service.list_tables(status='lobby')['total'] == 0


def test_a_joining_agent_names_an_unnamed_seat_but_the_creator_name_wins(tmp_path):
    service = PlayService(tmp_path)
    made = service.create_game(GameConfig('dice_circuit', 'base', 3), ['agent', 'agent', 'human'], visibility='listed',
                               seat_names=[None, 'Chosen by creator', None])
    table = made['table_id']
    first = service.join_game(table, 0, name='Robin')
    assert first['display_name'] == 'Robin'
    second = service.join_game(table, 1, name='Ignored')
    assert second['display_name'] == 'Chosen by creator'
    with pytest.raises(ValueError):
        service.join_game(table, 2, name='<b>bad</b>')
    # The refused join did not take the seat; without a name the default stays.
    assert service.join_game(table, 2)['display_name'] == 'Human'
    assert [s['display_name'] for s in service.list_tables()['items'][0]['seats']] == ['Robin', 'Chosen by creator', 'Human']


def test_a_name_set_by_the_creator_in_the_lobby_also_wins_over_the_joining_name(tmp_path):
    service = PlayService(tmp_path)
    made = service.create_game(GameConfig('dice_circuit', 'base', 1), ['agent'])
    set_presentation(service, made['table_id'], made['creator_credential'], [{'seat': 0, 'display_name': 'Named in lobby'}])
    assert service.join_game(made['table_id'], 0, name='Agent name')['display_name'] == 'Named in lobby'


def test_http_create_and_join_carry_seat_names(tmp_path):
    from boardcraft.server.api import create_app
    service = PlayService(tmp_path)
    with TestClient(create_app(service, run_bots=False)) as client:
        config = {'game_id': 'dice_circuit', 'variant': 'base', 'players': 2}
        made = client.post('/api/tables', json={'config': config, 'seats': ['agent', 'human'], 'seat_names': [None, 'Alice']}).json()
        assert [s['display_name'] for s in made['seats']] == ['Agent', 'Alice']
        table = made['table_id']
        joined = client.post(f'/api/tables/{table}/join', json={'seat': 0, 'name': 'Robin'})
        assert joined.status_code == 200 and joined.json()['display_name'] == 'Robin'
        assert client.post(f'/api/tables/{table}/join', json={'seat': 1, 'name': 'Other'}).json()['display_name'] == 'Alice'
        assert client.post(f'/api/tables/{table}/join', json={'seat': 0, 'name': 'x' * 41}).status_code == 422
        assert client.post('/api/tables', json={'config': config, 'seats': ['agent', 'human'], 'seat_names': ['a' * 41, None]}).status_code == 422
        assert client.post('/api/tables', json={'config': config, 'seats': ['agent', 'human'], 'seat_names': ['<x>', None]}).status_code >= 400


def test_mcp_join_game_takes_an_optional_name_and_documents_it(tmp_path):
    from boardcraft.mcp.server import create_server
    from mcp.client import Client
    service = PlayService(tmp_path)

    async def run():
        async with Client(create_server(service)) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            join = tools['join_game']
            assert 'name' in join.input_schema['properties'] and 'name' not in join.input_schema.get('required', [])
            assert 'name' in join.description and 'creator' in join.description
            assert 'seat_names' in tools['create_game'].input_schema['properties']

            async def call(tool, **args):
                response = await client.call_tool(tool, args)
                assert not response.is_error, response
                return json.loads(response.content[0].text)
            made = await call('create_game', config={'game_id': 'dice_circuit', 'variant': 'base', 'players': 2},
                              seats=['agent', 'agent'], seat_names=[None, 'Creator pick'])
            assert (await call('join_game', table_id=made['table_id'], seat=0, name='Robin'))['display_name'] == 'Robin'
            assert (await call('join_game', table_id=made['table_id'], seat=1, name='Robin'))['display_name'] == 'Creator pick'
            assert [s['display_name'] for s in (await call('observe_game', table_id=made['table_id']))['seats']] == ['Robin', 'Creator pick']
    asyncio.run(run())
