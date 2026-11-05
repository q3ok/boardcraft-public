"""Shared local play, durable authorization and perspective journals."""
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from boardcraft.contracts import GameConfig


def service(path, seed=3):
    assert importlib.util.find_spec('boardcraft.server') is not None, 'Shared play service is missing'
    from boardcraft.server.service import PlayService
    return PlayService(path, seed_factory=lambda: seed, clock=lambda: 1000.)


def started(path, players=1, seed=3):
    app = service(path, seed)
    made = app.create_game(GameConfig('dice_circuit', 'base', players), ['human'] * players)
    sessions = [app.join_game(made['table_id'], seat) for seat in range(players)]
    app.start_game(made['table_id'], made['creator_credential'])
    return app, made, sessions


def move(app, table, token):
    choices = app.get_actions(table, token)
    return app.apply_action(table, token, choices['decision_id'], choices['actions'][0]['id'])


def test_discovery_rules_and_module_limits(tmp_path):
    app = service(tmp_path)
    game = app.list_games()[0]
    assert game['name'] == 'Dice Circuit'
    assert game['variants'] == ['base'] and game['min_players'] == 1 and game['max_players'] == 4
    assert game['bot_kinds'] == ['random', 'greedy', 'strategist']
    from boardcraft.registry import get_game
    assert app.get_rules('dice_circuit') == get_game('dice_circuit').rules_text()
    for config in (GameConfig('unknown', 'base', 1), GameConfig('dice_circuit', 'bad', 1), GameConfig('dice_circuit', 'base', 5)):
        with pytest.raises(ValueError):
            app.create_game(config, ['human'] * config.players)


def test_default_config_keeps_legacy_table_shape(tmp_path):
    app = service(tmp_path)
    created = app.create_game(GameConfig('dice_circuit', 'base', 1), ['human'])
    assert created['config'] == {'game_id': 'dice_circuit', 'variant': 'base', 'players': 1}
    with app.store.transaction() as db:
        assert app.store.load(db, created['table_id'])['config'] == created['config']


def test_restart_reads_legacy_table_config_without_settings(tmp_path):
    app, made, sessions = started(tmp_path)
    with app.store.transaction(True) as db:
        state = app.store.load(db, made['table_id'])
        state['config'] = {'game_id': 'dice_circuit', 'variant': 'base', 'players': 1}
        app.store.save(db, made['table_id'], state)
    restored = service(tmp_path)
    view = restored.get_view(made['table_id'], sessions[0]['credential'])
    assert view['config'] == {'game_id': 'dice_circuit', 'variant': 'base', 'players': 1}
    assert restored.get_actions(made['table_id'], sessions[0]['credential'])['actions']


@pytest.mark.parametrize('phase', ['bootstrap', 'replay'])
def test_rule_participants_keep_canonical_shape_in_bootstrap_and_replay(tmp_path, monkeypatch, phase):
    from dataclasses import replace
    from boardcraft.contracts import PlayCapabilities
    from boardcraft.games.dice_circuit.module import DiceCircuitModule
    from boardcraft.inference import catalog as catalog_api
    from boardcraft.server import service as service_api
    from boardcraft.server.storage import digest, encode

    class RuleOnlyModule(DiceCircuitModule):
        def info(self):
            return replace(super().info(), capabilities=PlayCapabilities())

        def encoder(self):
            raise AssertionError('Rule-only participants must not access an encoder')

    module = RuleOnlyModule()
    monkeypatch.setattr(catalog_api, 'get_game', lambda game_id: module)
    monkeypatch.setattr(service_api, 'get_game', lambda game_id: module)
    catalog = catalog_api.RuntimeBotCatalog()
    app = service_api.PlayService(tmp_path, seed_factory=lambda: 3, bot_catalog=catalog)
    made = app.create_game(GameConfig('dice_circuit', 'base', 1), ['random'])
    app.start_game(made['table_id'], made['creator_credential'])
    expected = [{'seat': 0, 'kind': 'builtin', 'bot_ref': catalog_api.reference_data(catalog.pin('dice_circuit', 'random')),
                 'display_name': 'Random bot'}]
    with app.store.transaction(True) as db:
        bootstrap = app.store.checked(db.execute('SELECT payload,checksum FROM bootstrap WHERE table_id=?', (made['table_id'],)).fetchone())
        if phase == 'bootstrap':
            assert bootstrap['participants'] == expected
            return
        # Supply the canonical stored participant shape independently of the writer.
        bootstrap['participants'] = expected
        raw = encode(bootstrap)
        db.execute('UPDATE bootstrap SET payload=?,checksum=? WHERE table_id=?', (raw, digest(raw), made['table_id']))
    for _ in range(400):
        if app.get_result(made['table_id'])['result'] is not None:
            break
        work, = app.bot_work(made['table_id'])
        assert app.apply_bot(work, app.choose_bot(work))
    assert app.get_result(made['table_id'])['result'] is not None
    frame = app.get_replay(made['table_id'], None, 0)
    assert frame['position'] == 0 and frame['total_positions'] > 1


def test_seats_start_auth_and_sanitized_views(tmp_path):
    app = service(tmp_path)
    made = app.create_game(GameConfig('dice_circuit', 'base', 2), ['human', 'random'])
    table = made['table_id']
    with pytest.raises(ValueError, match='ready'):
        app.start_game(table, made['creator_credential'])
    joined = app.join_game(table, 0)
    for seat in (0, 1, True, -1):
        with pytest.raises(ValueError):
            app.join_game(table, seat)
    with pytest.raises(ValueError):
        app.start_game(table, joined['credential'])
    app.start_game(table, made['creator_credential'])
    assert app.get_view(table)['observation']['viewer'] is None
    own = app.get_view(table, joined['credential'])
    assert own['observation']['viewer'] == 0
    for forbidden in ('seed', 'snapshot', 'record', 'creator_credential', 'credential', 'internal_revision'):
        assert forbidden not in json.dumps(own)
    with pytest.raises(ValueError):
        app.get_view(table, 'wrong')
    with pytest.raises(ValueError):
        app.apply_action(table, None, 'x', 'x')
    assert joined['credential'].encode() not in (tmp_path / 'play.sqlite3').read_bytes()


def test_two_instances_race_and_restart_keep_one_action_and_journal(tmp_path):
    app, made, sessions = started(tmp_path)
    table, token = made['table_id'], sessions[0]['credential']
    other = service(tmp_path)
    choices = app.get_actions(table, token)
    def submit(client):
        try:
            return client.apply_action(table, token, choices['decision_id'], choices['actions'][0]['id'])
        except ValueError:
            return None
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(submit, (app, other)))
    assert sum(item is not None for item in results) == 1
    assert app.get_view(table, token) == other.get_view(table, token)
    events = other.get_events(table, token, 0)
    assert [event['sequence'] for event in events['events']] == list(range(1, events['next_cursor'] + 1))
    assert app.get_events(table, token, events['next_cursor'])['events'] == []
    assert service(tmp_path).get_view(table, token) == other.get_view(table, token)


def test_complete_agent_workflow_and_safe_replay(tmp_path):
    app, made, sessions = started(tmp_path)
    table, token = made['table_id'], sessions[0]['credential']
    with pytest.raises(ValueError, match='completed'):
        app.get_replay(table, token, 0)
    for _ in range(400):
        if app.get_result(table)['result'] is not None:
            break
        choices = app.get_actions(table, token)
        assert all(action['label'] and 'parameter_types' in action for action in choices['actions'])
        move(app, table, token)
    assert app.get_result(table)['result']['placements'] == [1]
    replay = app.get_replay(table, token, 0)
    assert replay['position'] == 0 and replay['total_positions'] > 0
    assert replay['observation']['revision'] < app.get_view(table, token)['observation']['revision']
    assert 'seed' not in json.dumps(replay) and 'snapshot' not in json.dumps(replay)
    final = app.get_replay(table, token, replay['total_positions'] - 1)
    assert final['observation'] == app.get_view(table, token)['observation']
    cursor, journal = 0, []
    while True:
        page = app.get_events(table, token, cursor, 7)
        assert len(page['events']) <= 7
        if not page['events']:
            break
        journal.extend(page['events'])
        cursor = page['next_cursor']
    assert [event['sequence'] for event in journal] == list(range(1, cursor + 1))
    assert cursor == final['observation']['data']['event_cursor']
    for bad_cursor, bad_limit in ((True, 1), (-1, 1), (cursor + 1, 1), (0, 201)):
        with pytest.raises(ValueError):
            app.get_events(table, token, bad_cursor, bad_limit)


def test_corrupt_storage_fails_closed(tmp_path):
    app, made, sessions = started(tmp_path)
    import sqlite3
    with sqlite3.connect(tmp_path / 'play.sqlite3') as connection:
        connection.execute("UPDATE tables SET payload = '{}' WHERE table_id = ?", (made['table_id'],))
    with pytest.raises(ValueError, match='corrupt'):
        app.get_view(made['table_id'], sessions[0]['credential'])


def test_passive_other_seat_and_observer_responses_do_not_change(tmp_path):
    app, made, sessions = started(tmp_path, 3, 9)
    table = made['table_id']
    tokens = [item['credential'] for item in sessions]
    for _ in range(400):
        choices = [app.get_actions(table, token) for token in tokens]
        eligible = [seat for seat, choice in enumerate(choices) if choice['actions']]
        public = app.get_view(table)
        if public['observation']['data']['phase'] == 'passive' and len(eligible) > 1:
            actor, waiting = eligible[:2]
            before = (app.get_view(table, tokens[waiting]), app.get_actions(table, tokens[waiting]), app.get_events(table, tokens[waiting], 0), app.get_events(table, None, 0))
            move(app, table, tokens[actor])
            assert app.get_view(table) == public
            assert before == (app.get_view(table, tokens[waiting]), app.get_actions(table, tokens[waiting]), app.get_events(table, tokens[waiting], 0), app.get_events(table, None, 0))
            return
        move(app, table, tokens[eligible[0]])
    pytest.fail('Expected independently eligible passive players')


def test_bots_only_use_own_decision_and_cross_instance_duplicate_is_rejected(tmp_path):
    app = service(tmp_path, 0)
    made = app.create_game(GameConfig('dice_circuit', 'base', 1), ['random'])
    table = made['table_id']
    app.start_game(table, made['creator_credential'])
    work = app.bot_work(table)[0]
    choice = app.choose_bot(work)
    other = service(tmp_path)
    assert choice == other.choose_bot(other.bot_work(table)[0])
    assert app.apply_bot(work, choice) is True
    before = app.get_view(table)
    assert other.apply_bot(work, choice) is False
    assert app.get_view(table) == before


def test_runtime_data_directory_rejects_application_source():
    from pathlib import Path
    import boardcraft
    package = Path(boardcraft.__file__).resolve().parent
    checkout = Path(__file__).resolve().parents[2]
    roots = [package]
    if package.is_relative_to(checkout):
        roots.append(checkout)
    for path in (root / 'saves' for root in roots):
        with pytest.raises(ValueError, match='outside'):
            service(path)
        assert not path.exists()


def test_simultaneous_eligible_seats_can_submit_independently(tmp_path):
    app, made, sessions = started(tmp_path, 3, 9)
    table, tokens = made['table_id'], [item['credential'] for item in sessions]
    for _ in range(400):
        choices = [app.get_actions(table, token) for token in tokens]
        eligible = [seat for seat, choice in enumerate(choices) if choice['actions']]
        if app.get_view(table)['observation']['data']['phase'] == 'passive' and len(eligible) > 1:
            import threading
            barrier = threading.Barrier(2)
            clients = [service(tmp_path), service(tmp_path)]
            for client in clients:
                commit = client._commit_apply
                def synchronized_commit(prepared, original=commit, first=[True]):
                    if first[0]:
                        first[0] = False
                        barrier.wait(timeout=5)
                    return original(prepared)
                client._commit_apply = synchronized_commit
            before = app._read_state(table)['steps']
            def submit(item):
                client, seat = item
                choice = choices[seat]
                return client.apply_action(table, tokens[seat], choice['decision_id'], choice['actions'][0]['id'])
            with ThreadPoolExecutor(2) as pool:
                assert len(list(pool.map(submit, zip(clients, eligible[:2])))) == 2
            assert app._read_state(table)['steps'] == before + 2
            for token in [None, *tokens]:
                page = app.get_events(table, token, 0, 200)
                assert [event['sequence'] for event in page['events']] == list(range(1, page['next_cursor'] + 1))
            return
        move(app, table, tokens[eligible[0]])
    pytest.fail('Expected simultaneous passive decisions')


def test_bot_work_survives_another_passive_seats_private_action(tmp_path):
    app = service(tmp_path, 9)
    made = app.create_game(GameConfig('dice_circuit', 'base', 3), ['random'] * 3)
    table = made['table_id']
    app.start_game(table, made['creator_credential'])
    for _ in range(400):
        work = app.bot_work(table)
        if app.get_view(table)['observation']['data']['phase'] == 'passive' and len(work) > 1:
            first, waiting = work[:2]
            waiting_choice = app.choose_bot(waiting)
            assert app.apply_bot(first, app.choose_bot(first))
            assert app.apply_bot(waiting, waiting_choice)
            return
        assert app.apply_bot(work[0], app.choose_bot(work[0]))
    pytest.fail('Expected simultaneous passive bot decisions')


def test_missing_persisted_journal_entry_is_not_silently_ignored(tmp_path):
    app, made, sessions = started(tmp_path)
    import sqlite3
    with sqlite3.connect(tmp_path / 'play.sqlite3') as db:
        db.execute('DELETE FROM journals WHERE table_id=? AND viewer=0', (made['table_id'],))
    with pytest.raises(ValueError, match='corrupt'):
        app.get_events(made['table_id'], sessions[0]['credential'])


def test_truncated_record_fails_even_for_bootstrap_replay(tmp_path):
    app, made, sessions = started(tmp_path)
    table, token = made['table_id'], sessions[0]['credential']
    while app.get_result(table)['result'] is None:
        move(app, table, token)
    import sqlite3
    with sqlite3.connect(tmp_path / 'play.sqlite3') as db:
        db.execute('DELETE FROM steps WHERE table_id=? AND step=1', (table,))
    with pytest.raises(ValueError, match='corrupt'):
        app.get_replay(table, token, 0)
