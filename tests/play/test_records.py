"""Trusted local JSON recordings are complete, checked and atomically saved."""
from dataclasses import replace
import gzip
import importlib.util
import json
import os
import pytest
from boardcraft.contracts import Event, GameConfig
from boardcraft.registry import get_game


def api():
    assert importlib.util.find_spec('boardcraft.play') is not None, 'Complete recordings and replay are missing'
    from boardcraft.play.records import read_record, write_record
    from boardcraft.play.runner import run_game
    from boardcraft.play.replay import replay
    return run_game, write_record, read_record, replay


def match():
    run, _, _, _ = api()
    module = get_game('dice_circuit')
    return run(module, GameConfig('dice_circuit', 'base', 1), 3, [module.bot('random', 5)])


@pytest.mark.parametrize('version', ['1', '2'])
def test_default_settings_preserve_legacy_record_config(version, tmp_path):
    from boardcraft.play.records import GameRecord, ParticipantRecord, record_data, validate_record
    record = GameRecord(version, GameConfig('dice_circuit', 'base', 1), '1', '1', {}, (), (), False, None,
                        () if version == '1' else (ParticipantRecord(0, 'external'),))
    assert record_data(record)['config'] == {'game_id': 'dice_circuit', 'variant': 'base', 'players': 1}
    assert validate_record(record) == record
    _, write, read, _ = api()
    path = tmp_path / 'legacy.gz'
    write(record, path)
    assert read(path) == record


def test_rule_participant_roundtrip_uses_canonical_reference_shape(tmp_path):
    from boardcraft.play.records import GameRecord, ParticipantRecord, record_data, validate_record
    from boardcraft.inference.catalog import reference_data, reference_from_data
    from boardcraft.inference.spec import digest
    data = {'kind': 'builtin', 'compatibility': {'game_id': 'play_only', 'rules_version': '1',
            'observation_version': '1', 'action_version': '1'}, 'builtin_kind': 'rule',
            'behavior_version': '1', 'options_json': '{}'}
    ref = reference_from_data({**data, 'identity': digest({'domain': 'boardcraft-rule-reference-v1', **data})})
    record = GameRecord('2', GameConfig('play_only', 'base', 1), '1', '1', {}, (), (), False, None,
                        (ParticipantRecord(0, 'builtin', ref, 'Rule+'),))
    assert record_data(record)['participants'] == [{'seat': 0, 'kind': 'builtin',
        'bot_ref': reference_data(ref), 'display_name': 'Rule+'}]
    assert validate_record(record) == record
    _, write, read, _ = api()
    path = tmp_path / 'rule.gz'
    write(record, path)
    assert read(path) == record


def test_gzip_json_roundtrip_and_every_prefix_has_exact_snapshot_and_views(tmp_path):
    run, write, read, replay = api()
    module = get_game('dice_circuit')
    references = []
    class CaptureModule:
        game_id, rules_version, engine_version = module.game_id, module.rules_version, module.engine_version
        def new(self, config, seed):
            engine = module.new(config, seed)
            apply = engine.apply
            def capture():
                references.append((engine.snapshot(), {key: engine.observe(viewer) for key, viewer in [('0', 0), ('observer', None)]}))
            capture()
            def accepted(*args):
                events = apply(*args)
                capture()
                return events
            engine.apply = accepted
            return engine
    record = run(CaptureModule(), GameConfig('dice_circuit', 'base', 1), 3, [module.bot('random', 5)])
    assert record.version == '2' and record.complete
    assert record.initial_events and record.initial_snapshot and record.final_result
    assert record.decisions and all(s.explanation and s.events and s.decision.id for s in record.decisions)
    path = tmp_path / 'game.json.gz'
    write(record, path)
    assert gzip.decompress(path.read_bytes()).startswith(b'{')
    assert read(path) == record
    for prefix in range(len(record.decisions) + 1):
        engine = replay(record, through_decision=prefix)
        expected, views = references[prefix]
        assert engine.snapshot() == expected
        for viewer in (0, None):
            key = 'observer' if viewer is None else str(viewer)
            assert engine.observe(viewer) == views[key]
    assert replay(record).result() == record.final_result
    data = json.loads(gzip.decompress(path.read_bytes()))
    assert set(data['decisions'][0]) == {'decision', 'action', 'events', 'explanation'}


def test_atomic_failure_preserves_completed_file_and_cleans_temporary_file(tmp_path, monkeypatch):
    _, write, read, _ = api()
    record, path = match(), tmp_path / 'match.gz'
    write(record, path)
    before = path.read_bytes()
    def fail_replace(*args):
        raise OSError('Replacement failed')
    monkeypatch.setattr(os, 'replace', fail_replace)
    with pytest.raises(OSError):
        write(replace(record, complete=False, final_result=None), path)
    assert path.read_bytes() == before and read(path) == record
    assert list(tmp_path.iterdir()) == [path]


def test_compression_failure_preserves_completed_file(tmp_path, monkeypatch):
    _, write, _, _ = api()
    record, path = match(), tmp_path / 'match.gz'
    write(record, path)
    before = path.read_bytes()
    def fail_write(*args):
        raise OSError('Compression failed')
    monkeypatch.setattr(gzip.GzipFile, 'write', fail_write)
    with pytest.raises(OSError):
        write(record, path)
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_corrupt_incompatible_and_incomplete_records_fail_clearly(tmp_path):
    _, write, read, replay = api()
    record, path = match(), tmp_path / 'match.gz'
    write(record, path)
    payload = json.loads(gzip.decompress(path.read_bytes()))
    payload['version'] = '999'
    path.write_bytes(gzip.compress(json.dumps(payload).encode()))
    with pytest.raises(ValueError, match='version'):
        read(path)
    path.write_bytes(b'broken')
    with pytest.raises(ValueError, match='record'):
        read(path)
    incomplete = replace(record, complete=False, final_result=None)
    write(incomplete, path)
    assert not read(path).complete
    with pytest.raises(ValueError, match='incomplete'):
        replay(incomplete)
    corrupt_step = replace(record.decisions[0], events=())
    with pytest.raises(ValueError, match='events'):
        replay(replace(record, decisions=(corrupt_step,) + record.decisions[1:]))
    first = record.decisions[0]
    payload = dict(first.events[0].payload)
    assert payload['player'] == 0
    payload['player'] = False
    corrupt_event = Event(first.events[0].sequence, first.events[0].kind, payload)
    corrupt_step = replace(first, events=(corrupt_event,) + first.events[1:])
    with pytest.raises(ValueError, match='events'):
        replay(replace(record, decisions=(corrupt_step,) + record.decisions[1:]))
    with pytest.raises(ValueError, match='version'):
        replay(replace(record, engine_version='999'))
    for prefix in (-1, True, len(record.decisions) + 1):
        with pytest.raises(ValueError, match='prefix'):
            replay(record, through_decision=prefix)


@pytest.mark.parametrize('mutation', ['extra', 'owner', 'action', 'completion', 'nonfinite'])
def test_record_reader_rejects_malformed_json_fields(tmp_path, mutation):
    _, write, read, _ = api()
    path = tmp_path / 'bad.gz'
    write(match(), path)
    data = json.loads(gzip.decompress(path.read_bytes()))
    if mutation == 'extra':
        data['unknown'] = 0
    elif mutation == 'owner':
        data['decisions'][0]['decision']['player'] = True
    elif mutation == 'action':
        del data['decisions'][0]['action']['kind']
    elif mutation == 'completion':
        data['complete'] = 1
    else:
        data['initial_snapshot']['extra'] = float('nan')
    path.write_bytes(gzip.compress(json.dumps(data).encode()))
    with pytest.raises(ValueError, match='record'):
        read(path)


def test_replay_accepts_independently_eligible_passive_seats_in_recorded_order():
    _, _, _, replay = api()
    from boardcraft.play.records import GameRecord, RecordedDecision
    from boardcraft.play.runner import project_receipt
    module, config = get_game('dice_circuit'), GameConfig('dice_circuit', 'base', 3)
    engine = module.new(config, 2)
    initial, bootstrap = engine.snapshot(), engine.initial_events()
    steps, nonselector = [], False
    while not engine.is_over():
        decision = next(engine.observe(seat).decision for seat in reversed(range(3)) if engine.observe(seat).decision is not None)
        nonselector |= decision != engine.decision()
        selected = engine.legal_actions(decision.player)[0]
        events = engine.apply(decision.player, selected, decision.id)
        project_receipt(engine, events, 3)
        steps.append(RecordedDecision(decision, selected, events, 'Choose an eligible seat independently.'))
    assert nonselector
    record = GameRecord('1', config, '1', '1', initial, bootstrap, tuple(steps), True, engine.result())
    assert replay(record).snapshot() == engine.snapshot()


def test_runner_detaches_accepted_actions_from_bot_owned_parameter_dicts():
    run, _, _, replay = api()
    module = get_game('dice_circuit')
    random_bot = module.bot('random', 77)
    class MutatingBot:
        previous = None
        def choose(self, observation, legal_actions):
            if self.previous is not None:
                self.previous.params['changed_after_acceptance'] = True
            choice = random_bot.choose(observation, legal_actions)
            self.previous = choice.action
            return choice
    record = run(module, GameConfig('dice_circuit', 'base', 1), 3, [MutatingBot()])
    assert all('changed_after_acceptance' not in step.action.params for step in record.decisions)
    assert replay(record).is_over()


@pytest.mark.parametrize('config', [GameConfig('dice_circuit', 'other-variant', 1), GameConfig('dice_circuit', 'base', 2)])
def test_replay_prefix_zero_rejects_header_configuration_mismatch(config):
    _, _, _, replay = api()
    record = replace(match(), config=config, complete=False, final_result=None)
    with pytest.raises(ValueError, match='configuration|participant count'):
        replay(record, through_decision=0)


def test_record_reader_normalizes_invalid_deflate_blocks(tmp_path):
    _, _, read, _ = api()
    data = bytearray(gzip.compress(b'{"a":1}'))
    data[10] = 255
    path = tmp_path / 'invalid-deflate.gz'
    path.write_bytes(data)
    with pytest.raises(ValueError, match='record'):
        read(path)


@pytest.mark.parametrize('location', ['root', 'config', 'snapshot'])
def test_record_reader_rejects_duplicate_json_fields_recursively(tmp_path, location):
    _, write, read, _ = api()
    path = tmp_path / 'duplicate-fields.gz'
    write(match(), path)
    text = gzip.decompress(path.read_bytes()).decode()
    if location == 'root':
        text = text.replace('"version":"2"', '"version":"bad","version":"2"', 1)
    elif location == 'config':
        text = text.replace('"variant":"base"', '"variant":"other","variant":"base"', 1)
    else:
        text = text.replace('"initial_snapshot":{', '"initial_snapshot":{"format":"bad",', 1)
    path.write_bytes(gzip.compress(text.encode()))
    with pytest.raises(ValueError, match='Duplicate.*record'):
        read(path)
