"""HTTP adapter forwards sanitized shared play operations."""
import importlib.util
from fastapi.testclient import TestClient


def test_http_agent_workflow_and_rules(tmp_path):
    assert importlib.util.find_spec('boardcraft.server') is not None, 'HTTP play adapter is missing'
    from boardcraft.server.api import create_app
    from boardcraft.server.service import PlayService
    service = PlayService(tmp_path, seed_factory=lambda: 3)
    with TestClient(create_app(service, run_bots=False)) as client:
        assert client.get('/api/games').json()[0]['name'] == 'Dice Circuit'
        assert client.get('/api/games/dice_circuit/rules').text == service.get_rules('dice_circuit')
        made = client.post('/api/tables', json={'config': {'game_id': 'dice_circuit', 'variant': 'base', 'players': 1}, 'seats': ['human']}).json()
        table = made['table_id']
        joined = client.post(f'/api/tables/{table}/join', json={'seat': 0}).json()
        auth = {'Authorization': 'Bearer ' + joined['credential']}
        assert client.post(f'/api/tables/{table}/start', headers={'Authorization': 'Bearer ' + made['creator_credential']}).status_code == 200
        choices = client.get(f'/api/tables/{table}/actions', headers=auth).json()
        body = {'decision_id': choices['decision_id'], 'action_id': choices['actions'][0]['id']}
        assert client.post(f'/api/tables/{table}/actions', headers=auth, json=body).status_code == 200
        assert client.post(f'/api/tables/{table}/actions', headers=auth, json=body).status_code == 409
        assert client.get(f'/api/tables/{table}/view', headers={'Authorization': 'Bearer bad'}).status_code == 403
        assert client.post('/api/tables', json={'config': {'game_id': 'dice_circuit', 'variant': 'base', 'players': True}, 'seats': ['human']}).status_code == 422
        assert client.post('/api/tables', content=b'x' * 70000).status_code == 413


def test_validation_errors_do_not_echo_request_secrets(tmp_path):
    from boardcraft.server.api import create_app
    from boardcraft.server.service import PlayService
    with TestClient(create_app(PlayService(tmp_path), run_bots=False)) as client:
        response = client.post('/api/tables', json={'config': {'game_id': 'dice_circuit', 'variant': 'base', 'players': 1, 'seed': 'hidden-randomness'}, 'seats': ['human']})
        assert response.status_code == 422
        assert 'hidden-randomness' not in response.text and 'seed' not in response.text
