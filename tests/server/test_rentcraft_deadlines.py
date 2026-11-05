"""Durable decision timeouts use a controlled wall clock."""
import pytest
from boardcraft.contracts import GameConfig
from boardcraft.server.service import PlayService
from boardcraft.server.views import Conflict


def test_stale_timeout_noop(tmp_path):
    now = [1000.]
    service = PlayService(tmp_path, seed_factory=lambda: 4, clock=lambda: now[0])
    made = service.create_game(GameConfig('rentcraft', 'base', 2), ['agent'] * 2,
                               live={'agent_timeout': 10, 'autopilot': True})
    key = made['table_id']
    tokens = [service.join_game(key, i)['credential'] for i in range(2)]
    service.start_game(key, made['creator_credential'])
    actor = service.get_view(key)['observation']['data']['acting_seat']
    deadline = service.get_view(key, tokens[actor])['control']['deadline']
    assert deadline['at'] == 1010 and deadline['kind'] == 'decision'
    assert 'control' not in service.get_view(key)
    now[0] = 1009.9
    assert not service.tick(key)
    old = service._read_state(key)['revision']
    choices = service.get_actions(key, tokens[actor])
    service.apply_action(key, tokens[actor], choices['decision_id'], 'roll')
    now[0] = 1010
    service = PlayService(tmp_path, clock=lambda: now[0])
    assert not service.tick(key, old)
    current = service._read_state(key)
    now[0] = 1030
    assert service.tick(key, current['revision'])
    assert not service.tick(key, current['revision'])
    current_actor = current['deadline']['seat']
    view = service.get_view(key, tokens[current_actor])
    assert view['control']['autopilot'] and view['control']['can_reclaim']
    # the round the autopilot took over in ("You ran out of time in round N")
    assert view['control']['autopilot_round'] == view['observation']['data']['round']
    revision = service.get_actions(key, tokens[current_actor])['expected_revision']
    count = service._read_state(key)['steps']
    service.submit_command(key, tokens[current_actor], revision, 'reclaim', {})
    assert service._read_state(key)['steps'] == count
    assert not service.get_view(key, tokens[current_actor])['control']['autopilot']
    with pytest.raises(Conflict):
        service.submit_command(key, tokens[current_actor], revision, 'reclaim', {})

def position(tmp_path, phase, *, live=None, mode='human', setup=None):
    from boardcraft.games.rentcraft.actions import Phase
    from boardcraft.server import decision_clock
    now = [100.]
    service = PlayService(tmp_path, seed_factory=lambda: 3, clock=lambda: now[0])
    made = service.create_game(GameConfig('rentcraft', 'base', 3), [mode] * 3, live=live)
    key = made['table_id']
    tokens = [service.join_game(key, i)['credential'] for i in range(3)]
    service.start_game(key, made['creator_credential'])
    with service.store.transaction(True) as db:
        state = service.store.load(db, key)
        engine = service._engine(state)
        board = engine.game.state
        board.current = 0
        board.phase = Phase(phase)
        # This synthetic mid-game position has no complete accepted-move history.
        board.memory.model_history = None
        if setup:
            setup(board)
        state['snapshot'] = engine.snapshot()
        decision_clock.arm(state, engine, now[0])
        service.store.save(db, key, state)
    return service, key, tokens, now


@pytest.mark.parametrize('claimed', [False, True])


def test_window_wait_explicit_request_gets_normal_clock(tmp_path, claimed):
    service, key, tokens, now = position(tmp_path, 'post_roll', live={'agent_timeout': None}, mode='agent')
    choices = service.get_actions(key, tokens[0])
    service.apply_action(key, tokens[0], choices['decision_id'], 'end_turn')
    actor = service.get_view(key)['observation']['data']['acting_seat']
    token = tokens[actor]
    deadline = service.get_view(key, token)['control']['deadline']
    assert deadline['kind'] == 'window' and deadline['at'] == 103.
    if claimed:
        revision = service.get_actions(key, token)['expected_revision']
        service.submit_command(key, token, revision, 'request_trade', {})
        assert service.get_view(key, token)['control']['deadline'] is None
    now[0] += 600
    assert service.tick(key) is (not claimed)
    if not claimed:
        with service.store.transaction() as db:
            record = service.store.checked(db.execute('SELECT payload,checksum FROM steps ORDER BY step DESC').fetchone())
        assert record['action']['kind'] == 'pass' and record['actor'] == actor


@pytest.mark.parametrize('next_creditor,next_amount', [(2, 30), (1, 125)])
def test_full_liquidation_is_atomic_and_stops_at_next_debt(tmp_path, next_creditor, next_amount):
    import sqlite3
    from boardcraft.games.rentcraft.state import Debt
    def assets(board):
        board.players[0].cash = 0
        board.owner[1] = board.owner[3] = board.owner[5] = 0
        board.debts = [Debt(0, 1, 125), Debt(0, next_creditor, next_amount)]
    service, key, tokens, _ = position(tmp_path, 'raise_funds', setup=assets)
    choices = service.get_actions(key, tokens[0])
    assert 'liquidate' in [c['kind'] for c in choices['commands']]
    before = service._read_state(key)
    args = (key, tokens[0], choices['expected_revision'], 'liquidate', {}, choices['decision_id'])
    with service.store.transaction(True) as db:
        db.execute("CREATE TRIGGER refuse BEFORE INSERT ON steps WHEN NEW.step=2 BEGIN SELECT RAISE(ABORT, 'injected'); END")
    with pytest.raises(sqlite3.IntegrityError):
        service.submit_command(*args)
    assert service._read_state(key) == before
    with service.store.transaction(True) as db:
        assert db.execute('SELECT COUNT(*) FROM steps').fetchone()[0] == 0
        db.execute('DROP TRIGGER refuse')
    service.submit_command(*args)
    board = service.get_view(key)['observation']['data']
    assert [(d['creditor'], d['amount']) for d in board['debts']] == [(next_creditor, next_amount)]
    assert board['players'][1]['cash'] == 1625
    assert board['mortgaged'][1] and board['mortgaged'][5] and not board['mortgaged'][3]
    with service.store.transaction() as db:
        steps = [service.store.checked(row) for row in db.execute('SELECT payload,checksum FROM steps ORDER BY step')]
    assert [s['action']['kind'] for s in steps] == ['mortgage', 'mortgage']
    assert [s['before_revision'] for s in steps] == [0, 1]


def test_timeout_resignation_is_owned_by_the_expired_agent(tmp_path):
    service, key, tokens, now = position(tmp_path, 'pre_roll', mode='agent')
    now[0] += 60
    assert service.tick(key)
    assert service.get_view(key)['observation']['data']['players'][0]['bankrupt']
    with service.store.transaction() as db:
        operation = service.store.checked(db.execute('SELECT payload,checksum FROM steps').fetchone())
    assert operation['actor'] == 0 and operation['decision'] is None and operation['action']['kind'] == 'resign'
    actions = service.get_actions(key, tokens[0])
    assert not actions['commands'] and 'expected_revision' not in actions


def test_reclaim_does_not_postpone_another_automatic_move(tmp_path):
    service, key, tokens, now = position(tmp_path, 'pre_roll', mode='agent',
                                         live={'autopilot': True, 'agent_timeout': 1, 'bot_delay': 5})
    now[0] += 1
    assert service.tick(key)
    at = service._read_state(key)['automatic_at']
    # Another seat that timed out on an earlier turn returns while this seat is waiting.
    with service.store.transaction(True) as db:
        state = service.store.load(db, key)
        state['seats'][1]['autopilot'] = True
        service.store.save(db, key, state)
    now[0] += 2
    rev = service.get_actions(key, tokens[1])['expected_revision']
    service.submit_command(key, tokens[1], rev, 'reclaim', {})
    assert service._read_state(key)['automatic_at'] == at
    now[0] = at
    assert service.tick(key)
    with service.store.transaction() as db:
        step = service.store.checked(db.execute('SELECT payload,checksum FROM steps').fetchone())
    assert step['actor'] == 0 and step['decision']['player'] == 0 and step['action']['kind'] == 'roll'

def test_eliminated_autopilot_seat_has_no_reclaim_permission(tmp_path):
    from boardcraft.games.rentcraft.state import Debt
    def insolvent(board):
        board.players[0].cash = 0
        board.debts = [Debt(0, 1, 500)]
    service, key, tokens, now = position(tmp_path, 'raise_funds', mode='agent', setup=insolvent,
                                         live={'autopilot': True, 'agent_timeout': 1})
    now[0] += 1
    assert service.tick(key)
    assert service.tick(key)
    assert service.get_view(key)['observation']['data']['players'][0]['bankrupt']
    control = service.get_view(key, tokens[0])['control']
    assert not control['autopilot'] and not control['can_reclaim']
    assert control['commands'] == []


def test_pending_request_does_not_restart_another_agents_decision_timer(tmp_path):
    service, key, tokens, now = position(tmp_path, 'pre_roll', mode='agent', live={'agent_timeout': 10})
    first = service.get_actions(key, tokens[0])
    service.submit_command(key, tokens[0], first['expected_revision'], 'propose_trade',
                           {'target': 1, 'give_cash': 12}, first['decision_id'])
    timer = service.get_view(key, tokens[1])['control']['deadline']
    now[0] += 9
    revision = service.get_actions(key, tokens[2])['expected_revision']
    service.submit_command(key, tokens[2], revision, 'request_trade', {})
    revised = service.get_view(key, tokens[1])['control']['deadline']
    assert revised['at'] == timer['at'] and revised['decision_id'] != timer['decision_id']
    now[0] += 1
    assert service.tick(key)
    assert service.get_view(key)['observation']['data']['players'][1]['bankrupt']


def test_live_settings_validate_from_discovery_and_keep_game_rules_separate(tmp_path):
    service = PlayService(tmp_path)
    config = GameConfig('rentcraft', 'base', 2)
    mixed = service.create_game(config, ['agent', 'random'])
    bots = service.create_game(config, ['random'] * 2)
    mixed_live = service.get_view(mixed['table_id'])['live']
    assert mixed_live == dict(agent_timeout=60., autopilot=False, trade_window_wait_s=3., bot_delay=0.)
    assert service.get_view(bots['table_id'])['live']['trade_window_wait_s'] == 0
    assert mixed['seats'][0]['kind'] == 'agent' and 'settings' not in mixed['config']
    for bad in ({'agent_timeout': 0}, {'bot_delay': -1}, {'trade_window_wait_s': 61}, {'autopilot': 1}):
        with pytest.raises(ValueError):
            service.create_game(config, ['agent', 'random'], live=bad)
    with pytest.raises(ValueError):
        service.create_game(GameConfig('dice_circuit', 'base', 1), ['human'], live={'agent_timeout': 10})


@pytest.mark.parametrize('responder', ['agent', 'cautious'])
def test_off_turn_request_keeps_due_automatic_response(tmp_path, responder):
    now = [100.]
    service = PlayService(tmp_path, seed_factory=lambda: 19, clock=lambda: now[0])
    config = GameConfig('rentcraft', 'base', 3, {'max_trade_exchanges': 1, 'trade_windows': 'off'})
    actor = service.module('rentcraft').new(config, 19).decision().player
    target, third = (actor + 1) % 3, (actor + 2) % 3
    seats = ['agent'] * 3
    seats[target] = responder
    made = service.create_game(config, seats, live={'agent_timeout': 1, 'autopilot': True, 'bot_delay': 10})
    key = made['table_id']
    tokens = {i: service.join_game(key, i)['credential'] for i, kind in enumerate(seats) if kind == 'agent'}
    service.start_game(key, made['creator_credential'])

    def offer(sender, amount):
        allowed = service.get_actions(key, tokens[sender])
        service.submit_command(key, tokens[sender], allowed['expected_revision'], 'propose_trade',
                               {'target': target, 'give_cash': amount}, allowed['decision_id'])

    if responder == 'agent':
        offer(actor, 12)
        now[0] = 101.
        assert service.tick(key)
    else:
        now[0] = 101.
        offer(actor, 12)
    assert service._read_state(key)['automatic_at'] == 111.

    now[0] = 110.
    rev = service.get_actions(key, tokens[third])['expected_revision']
    service.submit_command(key, tokens[third], rev, 'request_trade', {})
    assert service._read_state(key)['automatic_at'] == 111.
    assert not service.tick(key) and not service.bot_work(key)
    now[0] = 111.
    if responder == 'agent':
        assert service.tick(key)
    else:
        work, = service.bot_work(key)
        assert work.seat == target and service.apply_bot(work, service.choose_bot(work))
    board = service.get_view(key)['observation']['data']
    assert (board['phase'], board['acting_seat']) == ('trade_window', third)
    offer(third, 15)
    assert service._read_state(key)['automatic_at'] == 121.
    assert not service.tick(key) and not service.bot_work(key)


def test_debt_context_and_full_bank_liquidation(tmp_path):
    """The debtor sees what the bank can raise and may run the whole bank liquidation as one command."""
    from boardcraft.games.rentcraft.state import Debt

    def indebted(board):
        board.players[0].cash = 10
        board.owner[1] = board.owner[3] = 0  # two brown spaces: mortgage values 30 + 30
        board.debts = [Debt(0, 1, 50)]
    service, key, tokens, now = position(tmp_path, 'raise_funds', setup=indebted)
    choices = service.get_actions(key, tokens[0])
    debt = choices['context']['debt']
    assert debt['amount'] == 50 and debt['cash'] == 10 and debt['bank_enough'] and debt['needed_from_trades'] == 0
    assert debt['max_cash_from_bank'] == 10 + debt['mortgage_now'] + debt['building_sales'] + debt['mortgage_after_sales']
    assert 'liquidate' in [c['kind'] for c in choices['commands']]
    assert 'context' not in service.get_actions(key, tokens[1])
    service.submit_command(key, tokens[0], choices['expected_revision'], 'liquidate', {}, choices['decision_id'])
    data = service.get_view(key, tokens[0])['observation']['data']
    assert data['phase'] != 'raise_funds' and not data['players'][0]['bankrupt']
    assert 'context' not in service.get_actions(key, tokens[0])
