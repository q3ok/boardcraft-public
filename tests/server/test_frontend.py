"""Portable static composition and safe projected live scores."""
from fastapi.testclient import TestClient
from boardcraft.server.api import create_app
from boardcraft.server.service import PlayService
from boardcraft.contracts import GameConfig


def test_api_only_and_explicit_static_composition(tmp_path):
    service = PlayService(tmp_path / 'data')
    with TestClient(create_app(service, run_bots=False)) as client:
        assert client.get('/').status_code == 404
    static = tmp_path / 'static'
    static.mkdir()
    (static / 'index.html').write_text('<h1>Boardcraft</h1>', encoding='utf-8')
    with TestClient(create_app(service, run_bots=False, static_dir=static)) as client:
        assert client.get('/').text == '<h1>Boardcraft</h1>'
        assert client.get('/api/games').json()[0]['name'] == 'Dice Circuit'


def test_live_scores_use_only_projected_sheets(tmp_path):
    from boardcraft.games.dice_circuit.scoring import score_sheet
    from boardcraft.games.dice_circuit.visible import visible_sheet
    service = PlayService(tmp_path, seed_factory=lambda: 9)
    made = service.create_game(GameConfig('dice_circuit', 'base', 3), ['human'] * 3)
    table = made['table_id']
    tokens = [service.join_game(table, p)['credential'] for p in range(3)]
    service.start_game(table, made['creator_credential'])
    for _ in range(200):
        observer = service.get_view(table)['observation']
        expected = [score_sheet(visible_sheet(s)) for s in observer['data']['sheets']]
        summaries = observer['data'].get('score_summary')
        assert summaries is not None, 'Live score summary is missing'
        assert [s['total'] for s in summaries] == [s.total for s in expected]
        if observer['data']['phase'] == 'passive':
            before = summaries
            seat = next(p for p in range(3) if service.get_actions(table, tokens[p])['actions'])
            actions = service.get_actions(table, tokens[seat])
            service.apply_action(table, tokens[seat], actions['decision_id'], actions['actions'][0]['id'])
            after = service.get_view(table)['observation']
            if after['data']['phase'] == 'passive':
                assert after['data']['score_summary'] == before
                return
        else:
            seat = observer['data']['active_player']
            actions = service.get_actions(table, tokens[seat])
            service.apply_action(table, tokens[seat], actions['decision_id'], actions['actions'][0]['id'])
    raise AssertionError('An unresolved simultaneous window was not reached')
