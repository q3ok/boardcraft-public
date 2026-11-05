"""Authorization, durable operations and seat-local memory at a Rentcraft table."""
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from boardcraft.contracts import GameConfig
from boardcraft.server.service import PlayService
from boardcraft.server.views import AccessDenied, Conflict


def table(service, seats=None, **kwargs):
    seats = seats or ['human'] * 3
    made = service.create_game(GameConfig('rentcraft', 'base', len(seats)), seats, **kwargs)
    key = made['table_id']
    tokens = [service.join_game(key, i)['credential'] if kind in ('human', 'agent') else None
              for i, kind in enumerate(seats)]
    service.start_game(key, made['creator_credential'])
    return key, tokens, made['creator_credential']


def rows(service, table_id):
    with service.store.transaction() as db:
        return {name: db.execute(f'SELECT * FROM {name} WHERE table_id=? ORDER BY rowid', (table_id,)).fetchall()
                for name in ('tables', 'bootstrap', 'journals', 'steps')}


def test_one_winner_for_same_revision(tmp_path, monkeypatch):
    service = PlayService(tmp_path, seed_factory=lambda: 19)
    key, tokens, _ = table(service)
    actor = service.get_view(key)['observation']['data']['acting_seat']
    other = (actor + 1) % 3
    turn = service.get_actions(key, tokens[actor])
    rev = service.get_actions(key, tokens[other])['expected_revision']
    commit = service._commit_apply
    gate = threading.Barrier(2)
    def synchronized(move):
        gate.wait(5)
        return commit(move)
    monkeypatch.setattr(service, '_commit_apply', synchronized)
    with ThreadPoolExecutor(2) as pool:
        jobs = [pool.submit(service.apply_action, key, tokens[actor], turn['decision_id'], 'roll'),
                pool.submit(service.submit_command, key, tokens[other], rev, 'request_trade', {})]
        outcomes = [j.exception() for j in jobs]
    assert outcomes.count(None) == 1
    assert sum(isinstance(e, Conflict) for e in outcomes) == 1
    data = rows(service, key)
    assert len(data['steps']) == 1
    assert json.loads(data['bootstrap'][0][1])['record_version'] == '3'
    op = json.loads(data['steps'][0][2])
    assert set(op) == {'decision', 'action', 'events', 'explanation', 'actor', 'before_revision'}
    assert op['before_revision'] == 0 and rev > 0
    assert (op['actor'], op['decision'] is None) in [(actor, False), (other, True)]


def test_seat_permissions_and_failed_transaction(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 19)
    key, tokens, creator = table(service)
    actor = service.get_view(key)['observation']['data']['acting_seat']
    seat = (actor + 1) % 3
    rev = service.get_actions(key, tokens[seat])['expected_revision']
    assert 'expected_revision' not in service.get_actions(key)
    foreign, foreign_tokens, _ = table(service)
    assert key != foreign
    for credential in (None, creator, 'expired', foreign_tokens[seat]):
        with pytest.raises(AccessDenied):
            service.submit_command(key, credential, rev, 'resign', {})
    before = rows(service, key)
    with service.store.transaction(True) as db:
        db.execute("CREATE TRIGGER refuse BEFORE INSERT ON journals BEGIN SELECT RAISE(ABORT, 'injected'); END")
    with pytest.raises(sqlite3.IntegrityError):
        service.submit_command(key, tokens[seat], rev, 'request_trade', {})
    assert rows(service, key) == before
    with service.store.transaction(True) as db:
        db.execute('DROP TRIGGER refuse')
    service.submit_command(key, tokens[seat], rev, 'request_trade', {})
    with pytest.raises(Conflict):
        service.submit_command(key, tokens[seat], rev, 'request_trade', {})
    rev = service.get_actions(key, tokens[seat])['expected_revision']
    service.submit_command(key, tokens[seat], rev, 'resign', {})
    assert 'expected_revision' not in service.get_actions(key, tokens[seat])
    assert len(rows(service, key)['steps']) == 2


def test_stateful_bot_cas_restart(tmp_path):
    live = PlayService(tmp_path / 'live', seed_factory=lambda: 19)
    control = PlayService(tmp_path / 'control', seed_factory=lambda: 19)
    seats = ['random', 'random']
    key = table(live, seats)[0]
    baseline = table(control, seats)[0]
    seen = set()
    for _ in range(24):
        work, = live.bot_work(key)
        stale, = live.bot_work(key)
        reference, = control.bot_work(baseline)
        choice = live.choose_bot(work)
        duplicate = live.choose_bot(stale)
        expected = control.choose_bot(reference)
        assert choice == duplicate == expected
        assert live.apply_bot(work, choice)
        assert control.apply_bot(reference, expected)
        accepted = live._read_state(key)
        assert accepted['seats'][work.seat]['bot_state'] == choice.state_after
        assert not live.apply_bot(stale, duplicate)
        assert live._read_state(key) == accepted
        if not seen:
            assert 'bot_state' not in accepted['seats'][1 - work.seat]
        seen.add(work.seat)
        live = PlayService(tmp_path / 'live')
        if len(seen) == 2 and work.state_before is not None:
            break
    assert len(seen) == 2 and work.state_before is not None
    view = json.dumps(live.get_view(key))
    assert all('"' + secret + '"' not in view for secret in ('bot_state', 'seed', 'rng', 'state_after'))

def test_table_and_tournament_pin_the_same_configured_builtin(tmp_path):
    from boardcraft.server.tournaments import TournamentInput
    service = PlayService(tmp_path)
    config = GameConfig('rentcraft', 'base', 2)
    bot = {'choice_id': 'calculator', 'options': {'min_reserve': 421}}
    made = service.create_game(config, [bot, 'human'])
    expected = made['seats'][0]['bot_ref']
    assert expected['identity'] != service.bot_catalog.pin('rentcraft', 'calculator').identity
    with pytest.raises(ValueError):
        service.create_game(config, [{'choice_id': 'human', 'options': {}}, 'human'])
    args = dict(name='Two seats', config=dict(game_id='rentcraft', variant='base', players=2),
                rules_version='1', engine_version='1', seats=[bot, 'random'], seeds=[1],
                sampler_seeds=[[2, 3]], rotations=[0], match_count=1, schedule_cap=1,
                max_inflight=1, max_record_bytes=100000, max_decisions=100)
    assert TournamentInput.model_validate(args).model_dump()['seats'][0] == bot
    saved = service.create_tournament(args)
    try:
        assert service.get_tournament(saved['tournament_id'])['spec']['references'][0] == expected
    finally:
        service.close_tournaments()

def test_computed_choice_cannot_be_committed_to_another_table(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 19)
    first, _, _ = table(service, ['random', 'random'])
    second, _, _ = table(service, ['random', 'random'])
    work, = service.bot_work(first)
    foreign, = service.bot_work(second)
    assert not service.apply_bot(foreign, service.choose_bot(work))
    assert service._read_state(second)['steps'] == 0


def test_command_dto_rejects_identity_in_params():
    from boardcraft.server.schemas import CommandInput
    for field in ('player', 'actor', 'proposer'):
        with pytest.raises(ValueError):
            CommandInput.model_validate(dict(kind='propose_trade', expected_revision=7, params={field: 0}))

def test_parametric_rule_bot_uses_engine_legality_and_commits_memory(tmp_path):
    from dataclasses import replace
    from boardcraft.contracts import Action
    service = PlayService(tmp_path, seed_factory=lambda: 19)
    key, _, _ = table(service, ['hustler'] * 2)
    work, = service.bot_work(key)
    choice = service.choose_bot(work)
    assert choice.action.kind == 'propose_trade' and choice.action not in work.actions
    before = rows(service, key)
    invalid = Action('propose_trade', 'propose_trade', {'target': work.seat, 'give_cash': 1})
    with pytest.raises(Conflict):
        service.apply_bot(work, replace(choice, action=invalid))
    assert rows(service, key) == before
    assert service.apply_bot(work, choice)
    op = json.loads(rows(service, key)['steps'][0][2])
    assert op['action']['params'] == choice.action.params
    assert op['decision']['player'] == op['actor'] == work.seat
    assert service._read_state(key)['seats'][work.seat]['bot_state'] == choice.state_after
