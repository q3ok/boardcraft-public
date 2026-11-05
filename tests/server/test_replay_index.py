"""Indexed replays: identical frames, fast positions after the first request, a timeline and published window entries."""
import random
import time

from boardcraft.contracts import GameConfig
from boardcraft.server import replay as server_replay
from boardcraft.server.service import PlayService


def finished(service, config, seats):
    made = service.create_game(config, seats)
    table = made['table_id']
    service.start_game(table, made['creator_credential'])
    while service.get_result(table)['result'] is None:
        for work in service.bot_work(table):
            service.apply_bot(work, service.choose_bot(work))
    return table


def test_rentcraft_positions_match_a_full_traversal_and_are_fast_after_the_first_request(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 4)
    table = finished(service, GameConfig('rentcraft', 'base', 2, {'max_rounds': 6}), ['random', 'random'])
    first = service.get_replay(table, position=0)
    total = first['total_positions']
    assert total > 3 * server_replay.CHECKPOINT_EVERY
    state, viewer, bootstrap, steps, _ = service._replay_source(table, None)
    record, module = server_replay._table_record(service, state, bootstrap, steps)
    for position in (0, 1, server_replay.CHECKPOINT_EVERY - 1, server_replay.CHECKPOINT_EVERY, total // 2, total - 1):
        assert service.get_replay(table, position=position) == server_replay.project_record(record, module, None, position)
    timings = []
    for position in random.Random(3).sample(range(total), 30):
        started = time.perf_counter()
        service.get_replay(table, position=position)
        timings.append(time.perf_counter() - started)
    timings.sort()
    assert timings[len(timings) // 2] < 0.1, f'median {timings[len(timings) // 2]:.3f}s per position'
    assert timings[-1] < 0.3, f'slowest {timings[-1]:.3f}s per position'


def test_rentcraft_timeline_lists_every_position_with_net_worth_sets_and_hotels(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 6)
    table = finished(service, GameConfig('rentcraft', 'base', 2, {'max_rounds': 5}), ['random', 'random'])
    timeline = service.get_replay_timeline(table)
    assert len(timeline['positions']) == timeline['total_positions'] == service.get_replay(table)['total_positions']
    assert [item['position'] for item in timeline['positions']] == list(range(timeline['total_positions']))
    assert set(timeline['positions'][0]['metrics']) == {'net_worth', 'sets', 'hotels', 'bankrupt'}
    result = service.get_result(table)['result']
    if result['reason'] == 'round_limit':
        assert timeline['positions'][-1]['metrics']['net_worth'] == result['scores']
    assert all(set(event) == {'kind', 'player'} for item in timeline['positions'] for event in item['events'])


def test_a_changed_record_is_validated_again_instead_of_served_from_the_cache(tmp_path):
    from boardcraft.server.storage import digest, encode
    service = PlayService(tmp_path, seed_factory=lambda: 2)
    table = finished(service, GameConfig('rentcraft', 'base', 2, {'max_rounds': 2}), ['random', 'random'])
    service.get_replay(table, position=1)
    with service.store.transaction(True) as db:
        boot = service.store.checked(db.execute('SELECT payload, checksum FROM bootstrap').fetchone())
        payload = encode({**boot, 'engine_version': '2'})
        db.execute('UPDATE bootstrap SET payload=?,checksum=?', (payload, digest(payload)))
    try:
        service.get_replay(table, position=1)
    except ValueError as error:
        assert 'version' in str(error)
    else:
        raise AssertionError('A changed record must not be served from the cache')


def test_a_completed_listed_table_names_its_winning_seat(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 3)
    made = service.create_game(GameConfig('dice_circuit', 'base', 2), ['greedy', 'random'], visibility='listed')
    service.start_game(made['table_id'], made['creator_credential'])
    while service.get_result(made['table_id'])['result'] is None:
        for work in service.bot_work(made['table_id']):
            service.apply_bot(work, service.choose_bot(work))
    item, = service.list_tables()['items']
    placements = service.get_result(made['table_id'])['result']['placements']
    assert item['status'] == 'completed' and item['winner'] == placements.index(1)
    assert 'replay' not in item and 'replay_revision' not in item


def test_a_renamed_participant_is_detected_after_the_index_was_cached(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 5)
    table = finished(service, GameConfig('dice_circuit', 'base', 1), ['random'])
    service.get_replay(table, position=1)
    with service.store.transaction(True) as db:
        state = service.store.load(db, table)
        state['seats'][0]['display_name'] = 'Renamed'
        service.store.save(db, table, state)
    try:
        service.get_replay(table, position=1)
    except ValueError as error:
        assert 'participants' in str(error)
    else:
        raise AssertionError('A renamed participant must not be served from the cache')


def test_timeline_names_the_decision_of_each_position(tmp_path):
    service = PlayService(tmp_path, seed_factory=lambda: 9)
    table = finished(service, GameConfig('rentcraft', 'base', 2, {'max_rounds': 2}), ['random', 'random'])
    notes = [item['decision'] for item in service.get_replay_timeline(table)['positions'] if item['decision']]
    assert notes and all(set(n) >= {'player', 'action', 'explanation'} and n['action'] for n in notes)
    assert all('meta' not in n for n in notes), 'Built-in bots record no probabilities'
