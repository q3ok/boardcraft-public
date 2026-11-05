"""Operation records retain the engine's accepted history and seat-indexed outcome."""
import copy
from dataclasses import asdict, replace
import gzip
import hashlib
import json

import pytest

from boardcraft.contracts import GameConfig
from boardcraft.inference.catalog import builtin_reference
from boardcraft.play.records import ParticipantRecord, read_record, record_data, write_record
from boardcraft.play.replay import replay
from boardcraft.play.runner import run_game
from boardcraft.registry import get_game
from boardcraft.server.service import PlayService


def short_record():
    module = get_game('rentcraft')
    config = GameConfig('rentcraft', 'base', 2, {'max_rounds': 1})
    return run_game(module, config, 19, [module.bot('hustler', i) for i in (2, 3)])


def test_headless_parameterized_operations_roundtrip(tmp_path):
    record = short_record()
    assert record.version == '3'
    data = record_data(record)
    assert 'decisions' not in data and 'operations' in data
    assert data['config']['settings'] == {'max_rounds': 1}
    assert [op.before_revision for op in record.operations] == list(range(len(record.operations)))
    assert any(op.action.kind == 'propose_trade' and op.action.params for op in record.operations)
    assert record.final_result.reason == 'round_limit'
    assert set(data['final_result']) == {'scores', 'placements', 'reason', 'details'}
    path = tmp_path / 'game.json.gz'
    write_record(record, path)
    restored = read_record(path)
    assert restored == record and replay(restored).result() == record.final_result


def test_corrupt_operations_and_outcome_rejected():
    from boardcraft.play.records import _decode
    data = record_data(short_record())
    assert 'operations' in data
    def change(path, value):
        damaged = copy.deepcopy(data)
        target = damaged
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        return damaged
    operations = data['operations']
    for damaged in (
        change(['operations'], operations[1:] + operations[:1]),
        change(['operations', 0, 'before_revision'], 1),
        change(['operations', 0, 'actor'], 1 - operations[0]['actor']),
        change(['operations', 0, 'decision'], None),
        change(['operations', 0, 'action', 'params'], {}),
        change(['operations', 0, 'events'], []),
        change(['final_result', 'details', 'rounds'], True),
        change(['final_result', 'details', 'winner'], 1 - data['final_result']['details']['winner']),
        change(['final_result', 'scores', 0], data['final_result']['scores'][0] + 1),
        change(['final_result', 'placements'], [1, 1]),
        change(['final_result', 'area_scores'], []),
        change(['engine_version'], '2'),
    ):
        with pytest.raises(ValueError):
            replay(_decode(damaged))


@pytest.mark.parametrize('version,expected', [
    ('1', '33477a2eb40e8deb547d0f8807ab75db80adb31db400ea3eb37b5eac02832ece'),
    ('2', 'e8e27ccd71ac7217c3557745c6b3993d7295ec3d7bffd4657e04ff07d69f796e'),
])
def test_legacy_dice_bytes_and_historical_identity(version, expected, tmp_path):
    module = get_game('dice_circuit')
    ref = builtin_reference(module, 'random', '1')
    participant = ParticipantRecord(0, 'builtin', ref, 'Original name')
    record = run_game(module, GameConfig('dice_circuit', 'base', 1), 3, [module.bot('random', 5)],
                      participants=(participant,))
    if version == '1':
        record = replace(record, version='1', participants=())
    path = tmp_path / 'legacy.gz'
    write_record(record, path)
    assert hashlib.sha256(gzip.decompress(path.read_bytes())).hexdigest() == expected
    decoded = read_record(path)
    assert replay(decoded).result() == record.final_result
    assert decoded.participants == ((participant,) if version == '2' else (ParticipantRecord(0, 'unknown'),))


def test_operations_replay_each_prefix(tmp_path, monkeypatch):
    service = PlayService(tmp_path, seed_factory=lambda: 50)
    made = service.create_game(GameConfig('rentcraft', 'base', 3, {'trade_windows': 'off'}), ['human'] * 3)
    table = made['table_id']
    seats = [service.join_game(table, i)['credential'] for i in range(3)]
    service.start_game(table, made['creator_credential'])
    frames = {viewer: [] for viewer in [None, *seats]}
    cursors = dict.fromkeys(frames, 0)
    def capture():
        for viewer, collected in frames.items():
            page = service.get_events(table, viewer, cursors[viewer], 200)
            cursors[viewer] = page['next_cursor']
            collected.append((service.get_view(table, viewer)['observation'], page['events']))
    capture()
    # Seat 0 wins the auction, then mortgages its title while responding to the counteroffer.
    moves = [
        (2, 'roll', None), (2, 'decline', None), (0, 'bid:50', None),
        (1, 'pass_bid', None), (2, 'pass_bid', None), (2, 'end_turn', None),
        (0, 'propose_trade', {'target': 2, 'give_cash': 1}),
        (1, 'request_trade', {}), (2, 'counter_trade', {'give_cash': 60, 'get_spaces': [3]}),
        (0, 'mortgage:3', None), (0, 'accept_trade', None), (2, 'unmortgage:3', None),
        (1, 'pass', None), (2, 'resign', {}), (1, 'resign', {}),
    ]
    for seat, kind, params in moves:
        token = seats[seat]
        available = service.get_actions(table, token)
        if params is None:
            service.apply_action(table, token, available['decision_id'], kind)
        else:
            decision_id = None if kind in ('request_trade', 'resign') else available['decision_id']
            service.submit_command(table, token, available['expected_revision'], kind, params, decision_id)
        capture()
    final = service.get_result(table)['result']
    assert final['placements'] == [1, 2, 3] and final['reason'] == 'last_player'
    service = PlayService(tmp_path)
    monkeypatch.setattr(service.bot_catalog, 'resolve', lambda *_: pytest.fail('Replay resolved a bot'))
    assert service.get_result(table)['result'] == final
    for token, expected in frames.items():
        for prefix, (view, events) in enumerate(expected):
            frame = service.get_replay(table, token, prefix)
            assert frame == dict(position=prefix, total_positions=len(expected), observation=view, events=events)
            assert all(f'"{key}"' not in json.dumps(frame) for key in ('rng', 'seed', 'decks', 'bot_state'))


def test_simulation_defaults_are_frozen_at_creation(tmp_path):
    from boardcraft.play.runner import simulation_config
    from boardcraft.play.tournaments import TournamentSpec
    from boardcraft.play.ranking import RatingKey
    module = get_game('rentcraft')
    service = PlayService(tmp_path)
    assert service.get_settings('rentcraft')['simulation_defaults'] == {'max_rounds': 200}
    assert service.get_settings('dice_circuit')['simulation_defaults'] == {}
    settings = service.get_settings('rentcraft')['table']['properties']
    assert settings['max_rounds']['default'] is None
    keys = []
    for requested, expected in [({}, 200), ({'max_rounds': None}, None), ({'max_rounds': 2}, 2)]:
        config = GameConfig('rentcraft', 'base', 2, requested)
        pinned = simulation_config(module, config)
        assert pinned.settings == {'max_rounds': expected}
        assert simulation_config(module, pinned) == pinned
        made = service.create_tournament(dict(name='Caps', config=asdict(config), rules_version='1', engine_version='1',
            seats=['random', 'random'], seeds=[1], sampler_seeds=[[2, 3]], rotations=[0], match_count=1,
            schedule_cap=1, max_inflight=1, max_record_bytes=1000000, max_decisions=200))
        spec = service.get_tournament(made['tournament_id'])['spec']
        assert spec['config']['settings'] == pinned.settings and TournamentSpec.from_data(spec).data() == spec
        keys.append(RatingKey.from_config(pinned, '1').data())
    finite_default, unlimited, finite_custom = keys
    assert finite_default['settings_digest'] != finite_custom['settings_digest']
    assert 'settings_digest' not in unlimited
    for actual in ({}, {'max_rounds': None}, {'start_cash': 1500}, {'max_rounds': None, 'start_cash': 1500}):
        assert RatingKey.from_config(GameConfig('rentcraft', 'base', 2, actual), '1').data() == unlimited
    with_redundant_default = GameConfig('rentcraft', 'base', 2, {'max_rounds': 2, 'start_cash': 1500})
    assert RatingKey.from_config(with_redundant_default, '1').data() == finite_custom
    assert 'settings_digest' not in RatingKey.from_config(GameConfig('dice_circuit', 'base', 2), '1').data()
    service.close_tournaments()


def test_v3_config_rejects_boolean_as_round_limit():
    from boardcraft.play.records import _decode
    record = short_record()
    data = record_data(record)
    damaged = copy.deepcopy(data)
    damaged['config']['settings']['max_rounds'] = True
    with pytest.raises(ValueError):
        replay(_decode(damaged))


def test_equivalent_v3_json_has_one_compressed_digest(tmp_path):
    from boardcraft.play.records import _decode
    record = short_record()
    data = record_data(record)
    first, second = tmp_path / 'a.gz', tmp_path / 'b.gz'
    write_record(record, first)
    # A database or another JSON encoder may return equivalent object keys in a different order.
    sorted_data = json.loads(json.dumps(data, sort_keys=True))
    write_record(_decode(sorted_data), second)
    assert first.read_bytes() == second.read_bytes()
