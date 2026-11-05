"""Live-setting presentation metadata never changes the game timer policy."""
from fastapi.testclient import TestClient
from boardcraft.server import decision_clock
from boardcraft.server.api import create_app
from boardcraft.server.service import PlayService


def test_settings_api_marks_only_the_context_dependent_defaults_as_automatic(tmp_path):
    client = TestClient(create_app(PlayService(tmp_path)))
    live = client.get('/api/games/rentcraft/settings').json()['live']
    properties = live['properties']
    assert properties['trade_window_wait_s']['automatic'] is True
    assert [key for key, field in properties.items() if field.get('automatic')] == ['trade_window_wait_s', 'bot_delay']
    assert client.get('/api/games/dice_circuit/settings').json()['live']['properties'] == {}


def test_automatic_hint_does_not_change_defaults_or_explicit_overrides():
    people = [{'kind': 'human'}, {'kind': 'agent'}]
    bots = [{'kind': 'greedy'}, {'kind': 'greedy'}]
    assert decision_clock.live_settings('rentcraft', people, {})['trade_window_wait_s'] == 3.
    assert decision_clock.live_settings('rentcraft', bots, {})['trade_window_wait_s'] == 0.
    assert decision_clock.live_settings('rentcraft', bots, {'trade_window_wait_s': 7})['trade_window_wait_s'] == 7
    assert 'automatic' not in decision_clock.live_settings('rentcraft', people, {})
