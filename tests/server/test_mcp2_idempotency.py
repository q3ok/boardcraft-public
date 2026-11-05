"""K3: lost acknowledgements, atomic replay, scoped keys and protected credentials."""
from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from boardcraft.contracts import GameConfig
from mcp2_synthetic import SyntheticService, Catalog, table

KEY = 'test-client-random-key-000000000001'


def service_at(path, clock=lambda: 1000.):
    return SyntheticService(path, seed_factory=lambda: 37, bot_catalog=Catalog(), clock=clock)


def test_create_and_join_recover_credentials_after_restart_without_storing_them(tmp_path):
    service = service_at(tmp_path)
    config = GameConfig('mcp2_synthetic', 'base', 2)
    made = service.create_game(config, ['human'] * 2, idempotency_key=KEY)
    joined = service.join_game(made['table_id'], 0, idempotency_key=KEY)
    restarted = service_at(tmp_path)
    assert restarted.create_game(config, ['human'] * 2, idempotency_key=KEY) == made
    assert restarted.join_game(made['table_id'], 0, idempotency_key=KEY) == joined
    with service.store.transaction() as db:
        persisted = json.dumps(db.execute('SELECT * FROM idempotency').fetchall())
    for secret in (KEY, made['creator_credential'], joined['credential']):
        assert secret not in persisted
    with pytest.raises(ValueError):
        restarted.create_game(config, ['agent'] * 2, idempotency_key=KEY)
    with pytest.raises(ValueError):
        restarted.join_game(made['table_id'], 1, idempotency_key=KEY)
    # A different anonymous retry capability cannot recover the occupied seat.
    with pytest.raises(ValueError):
        restarted.join_game(made['table_id'], 0, idempotency_key=KEY + 'other')


def test_all_mutations_replay_original_envelopes_and_authority_is_bound(tmp_path):
    service = service_at(tmp_path)
    made = service.create_game(GameConfig('mcp2_synthetic', 'base', 2), ['human'] * 2)
    tid = made['table_id']
    tokens = [service.join_game(tid, p)['credential'] for p in range(2)]
    started = service.start_game(tid, made['creator_credential'], idempotency_key=KEY)
    assert service.start_game(tid, made['creator_credential'], idempotency_key=KEY) == started
    with pytest.raises(ValueError):
        service.start_game(tid, 'bad-credential', idempotency_key=KEY)
    first = service.get_view(tid, tokens[0])
    kwargs = dict(decision_token=first['decision_token'], idempotency_key=KEY)
    applied = service.apply_action(tid, tokens[0], 'choice-0', 'choose', **kwargs)
    second = service.get_view(tid, tokens[1])
    command = dict(decision_token=second['decision_token'], idempotency_key=KEY)
    completed = service.submit_command(tid, tokens[1], None, 'choose', {}, 'choice-1', **command)
    assert completed['status'] == 'completed'
    restarted = service_at(tmp_path)
    assert restarted.apply_action(tid, tokens[0], 'choice-0', 'choose', **kwargs) == applied
    assert restarted.submit_command(tid, tokens[1], None, 'choose', {}, 'choice-1', **command) == completed
    assert restarted.start_game(tid, made['creator_credential'], idempotency_key=KEY) == started
    with pytest.raises(ValueError):
        restarted.apply_action(tid, tokens[0], 'choice-0', 'different', **kwargs)


def test_concurrent_retry_creates_one_table_and_failed_mutation_does_not_reserve_key(tmp_path):
    services = [service_at(tmp_path), service_at(tmp_path)]
    def create(s):
        return s.create_game(GameConfig('mcp2_synthetic', 'base', 2), ['human'] * 2, idempotency_key=KEY)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(create, services))
    assert results[0] == results[1]
    with services[0].store.transaction() as db:
        assert db.execute('SELECT count(*) FROM tables').fetchone()[0] == 1
    tid = results[0]['table_id']
    with pytest.raises(ValueError):
        services[0].join_game(tid, 99, idempotency_key=KEY)
    assert services[0].join_game(tid, 0, idempotency_key=KEY)['seat'] == 0


def test_retention_expires_recovery_and_keys_are_validated(tmp_path):
    now = [1000.]
    service = service_at(tmp_path, clock=lambda: now[0])
    config = GameConfig('mcp2_synthetic', 'base', 2)
    first = service.create_game(config, ['human'] * 2, idempotency_key=KEY)
    now[0] += 24 * 60 * 60 + 1
    second = service.create_game(config, ['human'] * 2, idempotency_key=KEY)
    assert first['table_id'] != second['table_id']
    for key in ('', 'short', 'x' * 257, '\n' * 32):
        with pytest.raises(ValueError):
            service.create_game(config, ['human'] * 2, idempotency_key=key)


def test_reclaim_replay_preserves_exact_original_server_time(tmp_path):
    from boardcraft.server.service import PlayService
    now = [1000.]
    def clock():
        now[0] += .01
        return now[0]
    service = PlayService(tmp_path, seed_factory=lambda: 19, clock=clock)
    made = service.create_game(GameConfig('rentcraft', 'base', 2), ['agent'] * 2, live={'autopilot': True})
    tid = made['table_id']
    tokens = [service.join_game(tid, seat)['credential'] for seat in range(2)]
    started = service.start_game(tid, made['creator_credential'])
    actor = started['observation']['data']['acting_seat']
    with service.store.transaction(True) as db:
        state = service.store.load(db, tid)
        state['seats'][actor]['autopilot'] = True
        service.store.save(db, tid, state)
    view = service.get_view(tid, tokens[actor])
    args = dict(decision_token=view['decision_token'], idempotency_key=KEY)
    response = service.submit_command(tid, tokens[actor], None, 'reclaim', {}, **args)
    assert service.submit_command(tid, tokens[actor], None, 'reclaim', {}, **args) == response
