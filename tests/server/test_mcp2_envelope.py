"""K1: one service snapshot, perspective tokens and independent decisions."""
from concurrent.futures import ThreadPoolExecutor
import pytest
from boardcraft.registry import list_games
from mcp2_synthetic import table


def test_envelope_contains_actions_commands_and_perspective_from_one_read(tmp_path, monkeypatch):
    service, tid, tokens = table(tmp_path)
    reads = []
    original = service._read_state
    def read(t):
        reads.append(t)
        return original(t)
    monkeypatch.setattr(service, '_read_state', read)
    view = service.get_view(tid, tokens[0])
    assert view.get('contract_version') == '1'
    assert reads == [tid]
    assert view['perspective'] == {'role': 'seat', 'seat': 0}
    assert view['decision'] == {'id': 'choice-0', 'player': 0, 'kind': 'choose'}
    assert [a['id'] for a in view['actions']] == ['choose']
    assert view['commands'] == []
    assert view['decision_token'] and view['change_token']
    assert view['deadline'] is None and view['inaction_effect'] is None
    assert view['server_time'] == 1000.
    second = service.get_view(tid, tokens[1])
    assert second['actions'] == [] and second['commands'][0]['kind'] == 'choose'
    public = service.get_view(tid)
    assert public['perspective'] == {'role': 'observer', 'seat': None}
    assert public['decision_token'] is None
    assert 'mcp2_synthetic' not in [x.game_id for x in list_games()]


def test_hidden_move_does_not_change_other_tokens_or_require_global_revision(tmp_path):
    service, tid, tokens = table(tmp_path)
    before = service.get_view(tid, tokens[1])
    first = service.get_view(tid, tokens[0])
    moved = service.apply_action(tid, tokens[0], first['decision']['id'], 'choose', decision_token=first['decision_token'])
    assert moved['contract_version'] == '1'
    after = service.get_view(tid, tokens[1])
    assert before['decision_token'] == after['decision_token']
    assert before['change_token'] == after['change_token']
    assert before['event_cursor'] == after['event_cursor'] == 0
    result = service.submit_command(tid, tokens[1], None, 'choose', {}, 'choice-1', decision_token=before['decision_token'])
    assert result['status'] == 'completed' and result['contract_version'] == '1'
    with pytest.raises(ValueError):
        service.apply_action(tid, tokens[0], 'choice-0', 'choose', decision_token=first['decision_token'])


def test_simultaneous_seats_can_commit_with_their_original_tokens(tmp_path):
    service, tid, tokens = table(tmp_path)
    views = [service.get_view(tid, token) for token in tokens]
    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(service.apply_action, tid, tokens[0], 'choice-0', 'choose', decision_token=views[0]['decision_token'])
        b = pool.submit(service.submit_command, tid, tokens[1], None, 'choose', {}, 'choice-1', decision_token=views[1]['decision_token'])
        assert a.result()['contract_version'] == b.result()['contract_version'] == '1'
    assert service.get_view(tid)['status'] == 'completed'


def test_global_engine_ids_do_not_leak_hidden_moves_or_stale_independent_tokens(tmp_path, monkeypatch):
    from dataclasses import replace
    from mcp2_synthetic import Engine
    original = Engine.observe
    def observe(self, viewer):
        observation = original(self, viewer)
        decision = observation.decision
        if decision is not None:
            decision = replace(decision, id=f'global-{len(self.done)}-{viewer}')
        return replace(observation, revision=len(self.done), decision=decision)
    monkeypatch.setattr(Engine, 'observe', observe)
    service, tid, tokens = table(tmp_path)
    before = service.get_view(tid, tokens[1])
    own = service.get_view(tid, tokens[0])
    service.apply_action(tid, tokens[0], own['decision']['id'], 'choose', decision_token=own['decision_token'])
    after = service.get_view(tid, tokens[1])
    assert before['decision_token'] == after['decision_token']
    assert before['change_token'] == after['change_token']
    result = service.submit_command(tid, tokens[1], None, 'choose', {}, before['decision']['id'], decision_token=before['decision_token'])
    assert result['status'] == 'completed'


def test_next_identical_own_decision_does_not_accept_previous_token(tmp_path, monkeypatch):
    from mcp2_synthetic import Engine
    original = Engine.apply
    def repeat(self, player, action, decision_id):
        receipt = original(self, player, action, decision_id)
        self.done.remove(player)
        return receipt
    monkeypatch.setattr(Engine, 'apply', repeat)
    service, tid, tokens = table(tmp_path)
    before = service.get_view(tid, tokens[0])
    after = service.apply_action(tid, tokens[0], 'choice-0', 'choose', decision_token=before['decision_token'])
    assert after['decision_token'] != before['decision_token']
    with pytest.raises(ValueError):
        service.apply_action(tid, tokens[0], 'choice-0', 'choose', decision_token=before['decision_token'])
