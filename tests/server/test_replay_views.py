"""Completed replay positions include only the viewer's permitted frames."""
import pytest
from boardcraft.contracts import GameConfig
from boardcraft.server.service import PlayService


def test_observer_replay_has_no_invisible_private_chain_positions(tmp_path, monkeypatch):
    service = PlayService(tmp_path, seed_factory=lambda: 9)
    made = service.create_game(GameConfig('dice_circuit', 'base', 3), ['human'] * 3)
    table = made['table_id']
    tokens = [service.join_game(table, seat)['credential'] for seat in range(3)]
    service.start_game(table, made['creator_credential'])
    frames = {token: [service.get_view(table, token)['observation']] for token in [None, tokens[0]]}
    cursors = {token: service.get_events(table, token)['next_cursor'] for token in frames}
    frame_events = {token: [service.get_events(table, token)['events']] for token in frames}
    raw_steps = 0
    while service.get_result(table)['result'] is None:
        for token in tokens:
            actions = service.get_actions(table, token)
            if actions['actions']:
                service.apply_action(table, token, actions['decision_id'], actions['actions'][0]['id'])
                raw_steps += 1
                break
        for token in frames:
            page = service.get_events(table, token, cursors[token], 200)
            cursors[token] = page['next_cursor']
            if page['events']:
                frames[token].append(service.get_view(table, token)['observation'])
                frame_events[token].append(page['events'])
    assert len(frames[None]) < raw_steps + 1, 'Fixture must contain hidden-only accepted actions'
    automatic_positions = {token: {i for i, events in enumerate(pages)
                                  if any(e['kind'] == 'bonus_auto_resolved' for e in events)}
                           for token, pages in frame_events.items()}
    assert all(automatic_positions.values()), 'Fixture must contain visible automatic bonus events'
    from boardcraft.server import replay as server_replay
    engine_type = type(service._engine(service._read_state(table)))
    apply, recorded, calls, validated = engine_type.apply, server_replay.apply_recorded, [], []
    def counted_apply(self, *args):
        calls.append(1)
        return apply(self, *args)
    def counted_recorded(engine, step):
        validated.append(1)
        return recorded(engine, step)
    monkeypatch.setattr(engine_type, 'apply', counted_apply)
    monkeypatch.setattr(server_replay, 'apply_recorded', counted_recorded)
    for token, expected in frames.items():
        validated.clear()
        for number, position in enumerate(sorted({0, 1, len(expected) // 2, len(expected) - 1, *automatic_positions[token]})):
            calls.clear()
            result = service.get_replay(table, token, position=position)
            assert set(result) == {'observation', 'events', 'position', 'total_positions'}
            assert result['position'] == position and result['total_positions'] == len(expected)
            assert result['observation'] == expected[position]
            assert result['events'] == frame_events[token][position]
            assert all('emblem_count' in sheet and 'emblems' not in sheet
                       for sheet in result['observation']['data']['sheets'])
            assert all('emblem_points' in score and 'emblems' not in score
                       for score in result['observation']['data']['score_summary'])
            assert position == 0 or result['events'], 'Hidden-only steps must not have replay positions'
            # The first request validates the whole history once; later positions start from a checkpoint.
            assert len(validated) == raw_steps, 'Replay must validate history once per viewer, not per position'
            if number:
                assert len(calls) <= raw_steps // 3, 'A later position must start from a nearby checkpoint'
        with pytest.raises(ValueError, match='Replay position is unavailable'):
            service.get_replay(table, token, position=len(expected))


def test_pre_participant_dice_table_finishes_after_upgrade(tmp_path, monkeypatch):
    from boardcraft.play.records import ParticipantRecord
    from boardcraft.server import replay as server_replay
    from boardcraft.server.storage import digest, encode

    service = PlayService(tmp_path, seed_factory=lambda: 3)
    made = service.create_game(GameConfig('dice_circuit', 'base', 1), ['random'])
    table = made['table_id']
    service.start_game(table, made['creator_credential'])
    work, = service.bot_work(table)
    assert service.apply_bot(work, service.choose_bot(work))
    with service.store.transaction(True) as db:
        state = service.store.load(db, table)
        for key in ('bot_ref', 'display_name', 'availability'):
            del state['seats'][0][key]
        service.store.save(db, table, state)
        bootstrap = service.store.checked(db.execute('SELECT payload, checksum FROM bootstrap').fetchone())
        del bootstrap['participants']
        payload = encode(bootstrap)
        db.execute('UPDATE bootstrap SET payload=?, checksum=?', (payload, digest(payload)))

    service = PlayService(tmp_path)
    for _ in range(300):
        if service.get_result(table)['result'] is not None:
            break
        work, = service.bot_work(table)
        assert service.apply_bot(work, service.choose_bot(work))
    assert service.get_result(table)['result'] is not None
    project = server_replay.project_record
    def check_historical_participants(record, *args):
        assert record.version == '1' and record.participants == (ParticipantRecord(0, 'unknown'),)
        return project(record, *args)
    monkeypatch.setattr(server_replay, 'project_record', check_historical_participants)
    monkeypatch.setattr(service.bot_catalog, 'resolve', lambda *_: pytest.fail('Replay resolved a legacy alias'))
    assert service.get_replay(table)['position'] == 0


def test_rentcraft_bootstrap_pins_both_versions(tmp_path):
    from boardcraft.server.storage import digest, encode

    service = PlayService(tmp_path, seed_factory=lambda: 2)
    made = service.create_game(GameConfig('rentcraft', 'base', 2), ['human'] * 2)
    table = made['table_id']
    seats = [service.join_game(table, i)['credential'] for i in range(2)]
    service.start_game(table, made['creator_credential'])
    actions = service.get_actions(table, seats[0])
    service.submit_command(table, seats[0], actions['expected_revision'], 'resign', {})
    with service.store.transaction() as db:
        boot = service.store.checked(db.execute('SELECT payload, checksum FROM bootstrap').fetchone())
    assert boot.get('rules_version') == boot.get('engine_version') == '1'
    assert service.get_replay(table)['position'] == 0
    for removed, changed in [
        (['rules_version', 'engine_version'], {}), (['engine_version'], {}), (['rules_version'], {}),
        (['record_version'], {}), ([], {'engine_version': '2'}), ([], {'rules_version': '2'}),
    ]:
        damaged = {key: value for key, value in boot.items() if key not in removed}
        payload = encode({**damaged, **changed})
        with service.store.transaction(True) as db:
            db.execute('UPDATE bootstrap SET payload=?,checksum=?', (payload, digest(payload)))
        with pytest.raises(ValueError, match='version'):
            service.get_replay(table)
