"""Instance-local library discovery and explicit immutable comparisons."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from mcp.client import Client

from boardcraft.server.api import create_app
from boardcraft.server.service import PlayService
from boardcraft.server.storage import digest, encode


def test_empty_library_does_not_initialize_tournaments(tmp_path, monkeypatch):
    for name in ('first', 'second'):
        service = PlayService(tmp_path / name)
        monkeypatch.setattr(service, 'tournaments', lambda: pytest.fail('Lazy tournament dispatch'))
        with TestClient(create_app(service, run_bots=False)) as client:
            for route in ('rankings', 'replays'):
                # The replay list also says whether this client may delete replays (open management only).
                assert client.get('/api/' + route).json() == {
                    'items': [], 'total': 0, 'offset': 0, 'limit': 25,
                    **({'management': 'credential', 'can_manage': False} if route == 'replays' else {})}
        assert not (service.store.directory / 'tournament-records-v1').exists()


@pytest.mark.parametrize('query', ['limit=0', 'limit=101', 'offset=-1', 'offset=true',
                                    'players=0', 'settings_digest=bad', 'unknown=x'])
def test_strict_ranking_queries(tmp_path, query):
    with TestClient(create_app(PlayService(tmp_path), run_bots=False)) as client:
        assert client.get('/api/rankings?' + query).status_code in (400, 422)


@pytest.mark.parametrize('query', ['limit=0', 'offset=-1', 'source=invite',
                                    'source=table&tournament_id=' + 'a' * 32, 'unknown=x'])
def test_strict_replay_queries(tmp_path, query):
    with TestClient(create_app(PlayService(tmp_path), run_bots=False)) as client:
        assert client.get('/api/replays?' + query).status_code in (400, 422)


def saved_fit(service, tournament_id='a' * 32):
    from boardcraft.play.tournaments import TournamentSpec
    from boardcraft.play.ranking import RatingKey, RatingFitSpec, fit_ratings
    from boardcraft.contracts import GameConfig
    from boardcraft.inference.catalog import builtin_reference
    from boardcraft.registry import get_game
    module = get_game('dice_circuit')
    ref = builtin_reference(module, 'random', '1')
    spec = TournamentSpec('Synthetic', GameConfig('dice_circuit', 'base', 2),
                          module.rules_version, module.engine_version, (ref, ref),
                          (1,), ((2, 3),), (0,), 1, 1, 1, 100000, 100)
    store = service.tournaments().store
    key = RatingKey.from_config(spec.config, spec.rules_version)
    fit = service.tournament_schema()['rating_profiles'][0]['fit_spec']
    result = fit_ratings([], key, fit_spec=RatingFitSpec(**fit))
    raw, spec_raw = encode(result), encode(spec.data())
    with store.transaction(True) as db:
        db.execute('INSERT INTO tournaments VALUES(?,?,?,?,?,?,?,?)',
                   (tournament_id, spec_raw, digest(spec_raw), digest('secret'), 'completed', 0, None, 0))
        db.execute('INSERT INTO tournament_ratings VALUES(?,?,?,?,?,?)',
                   (tournament_id, digest(encode(key.data())), digest(encode(fit)), digest('[]'), raw, digest(raw)))
    return result, fit


def test_saved_fit_paging_filters_pin_isolation_and_deletion(tmp_path, monkeypatch):
    service = PlayService(tmp_path / 'first')
    other = PlayService(tmp_path / 'second')
    result, _ = saved_fit(service)
    monkeypatch.setattr(service, 'tournaments', lambda: pytest.fail('GET initialized controller'))
    page = service.list_rankings(limit=1)
    assert page['total'] == 1 and len(page['items']) == 1
    item = page['items'][0]
    assert item['source'] == 'tournament_fit'
    assert service.list_rankings(game_id='rentcraft')['total'] == 0
    assert service.list_rankings(offset=1)['items'] == []
    assert other.list_rankings()['total'] == 0
    assert service.get_ranking(item['comparison_id'], item['result_sha'])['groups'] == [result]
    with pytest.raises(ValueError):
        service.get_ranking(item['comparison_id'], '0' * 64)
    assert 'secret' not in encode(page)
    with service.store.transaction(True) as db:
        db.execute("UPDATE tournaments SET status='deleted'")
    assert service.list_rankings()['total'] == 0
    with pytest.raises(ValueError):
        service.get_ranking(item['comparison_id'], item['result_sha'])


def test_mcp_empty_library_tools(tmp_path):
    from boardcraft.mcp.server import create_server
    async def check():
        async with Client(create_server(PlayService(tmp_path))) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert {'fit_rankings', 'list_rankings', 'get_ranking', 'list_replays'} <= names
            for name in ('list_rankings', 'list_replays'):
                result = await client.call_tool(name, {})
                assert not result.is_error
                assert json.loads(result.content[0].text)['items'] == []
    asyncio.run(check())


def completed_source(service, *, engine='1', settings=None, players=2):
    """Publish a fully synthetic canonical file and its durable completed binding."""
    import hashlib
    import uuid
    from boardcraft.contracts import GameConfig
    from boardcraft.games.dice_circuit.scoring import DiceCircuitResult
    from boardcraft.inference.catalog import builtin_reference, reference_data
    from boardcraft.play.records import GameRecord, ParticipantRecord, write_record
    from boardcraft.play.ranking import RatingKey
    from boardcraft.play.tournaments import TournamentSpec, schedule
    from boardcraft.registry import get_game
    module = get_game('dice_circuit')
    config = GameConfig('dice_circuit', 'base', players, settings or {})
    refs = tuple(builtin_reference(module, kind, '1') for kind in ('random', 'greedy')[:players])
    spec = TournamentSpec('Synthetic source', config, module.rules_version, engine,
                          refs, (1,), ((2, 3)[:players],), (0,), 1, 1, 1, 1000000, 100)
    ts = service.tournaments().store
    # Store the saved source directly: tests never dispatch games or a runner.
    id, token = uuid.uuid4().hex, uuid.uuid4().hex
    raw = encode(spec.data())
    match = schedule(spec, module=module)[0] if engine == module.engine_version else None
    if match is None:
        from dataclasses import replace
        match = schedule(replace(spec, engine_version=module.engine_version), module=module)[0]
    record = GameRecord('2', config, module.rules_version, engine,
                        module.new(config, 1).snapshot(), (), (), True,
                        DiceCircuitResult((3, 1)[:players], tuple(
                            {'yellow': score, 'blue': 0, 'green': 0, 'orange': 0, 'purple': 0}
                            for score in (3, 1)[:players]), (0, 0)[:players], (1, 2)[:players], 'Synthetic'),
                        tuple(ParticipantRecord(i, ref.kind, ref) for i, ref in enumerate(refs)))
    path = ts.root / (token + '.fixture.json.gz')
    write_record(record, path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    size = path.stat().st_size
    path.rename(ts.root / (sha + '.json.gz')) if not (ts.root / (sha + '.json.gz')).exists() else path.unlink()
    result = dict(key=RatingKey.from_config(config, module.rules_version).data(), engine_version=engine,
                  identities=[r.identity for r in refs], participants=[reference_data(r) for r in refs],
                  placements=[1, 2][:players], scores=[3, 1][:players], record_sha256=sha, record_size=size,
                  match_id=match.match_id, attempt_token=token)
    result_raw, match_raw = encode(result), encode(match.data())
    with ts.transaction(True) as db:
        db.execute('INSERT INTO tournaments VALUES(?,?,?,?,?,?,?,?)', (id, raw, digest(raw), digest('secret'), 'completed', 0, None, 0))
        db.execute('INSERT INTO tournament_matches VALUES(?,?,?,?,?,?,?,?,?)', (id, match.match_id, 0, match_raw, digest(match_raw), 'completed', token, result_raw, digest(result_raw)))
        db.execute('INSERT INTO tournament_attempts VALUES(?,?,?,?,?,?,?,?,?)', (token, id, match.match_id, 'run', 'completed', 'stage', 'temp', sha, None))
        db.execute('INSERT OR IGNORE INTO tournament_records VALUES(?,?,?,?)', (sha, size, sha + '.json.gz', 'ready'))
        db.execute('INSERT INTO tournament_record_refs VALUES(?,?,?,?,?)', (id, match.match_id, token, sha, 'completed'))
    return dict(tournament_id=id, spec_sha=digest(raw), revision=0, credential='secret'), result


def test_combined_fit_matches_existing_math_manifest_pin_and_frozen_sample(tmp_path, monkeypatch):
    from boardcraft.play.ranking import RatingFitSpec, RatingKey, fit_ratings
    
    service = PlayService(tmp_path)
    a, ra = completed_source(service)
    b, rb = completed_source(service)
    fit = service.tournament_schema()['rating_profiles'][0]['fit_spec']
    combined = service.fit_rankings([b, a], fit)
    assert combined['groups'] == [fit_ratings([ra, rb], RatingKey(**ra['key']), fit_spec=RatingFitSpec(**fit))]
    assert [s['tournament_id'] for s in combined['manifest']['sources']] == sorted([a['tournament_id'], b['tournament_id']])
    text = encode(combined)
    for private in ('credential', 'participants', 'relpath', 'creator_hash', 'architecture_json', 'snapshot'):
        assert private not in text
    assert 'result_sha' in combined['manifest']['sources'][0]['results'][0]
    again = service.fit_rankings([a, b], fit)
    assert again['comparison_id'] != combined['comparison_id']
    assert again['manifest_sha'] == combined['manifest_sha']
    monkeypatch.setattr(service, 'tournaments', lambda: pytest.fail('GET dispatched'))
    assert service.get_ranking(combined['comparison_id'], combined['result_sha']) == combined
    assert service.list_rankings(tournament_id=a['tournament_id'])['total'] == 2
    replays = service.list_replays(source='tournament', limit=1)
    assert replays['total'] == 2 and len(replays['items']) == 1
    assert set(replays['items'][0]['result']) == {'scores', 'placements'}
    with service.store.transaction(True) as db:
        db.execute("UPDATE tournaments SET status='deleted' WHERE tournament_id=?", (a['tournament_id'],))
    assert service.list_rankings()['items'] == []
    with pytest.raises(ValueError):
        service.get_ranking(combined['comparison_id'], combined['result_sha'])


@pytest.mark.parametrize('failure', ['credential', 'revision', 'spec', 'duplicate', 'binding', 'file'])
def test_combined_fit_rejects_bad_sources_without_publication(tmp_path, failure):
    service = PlayService(tmp_path)
    source, result = completed_source(service)
    fit = service.tournament_schema()['rating_profiles'][0]['fit_spec']
    selected = [source]
    if failure == 'credential': source['credential'] = 'wrong'
    if failure == 'revision': source['revision'] = 1
    if failure == 'spec': source['spec_sha'] = '0' * 64
    if failure == 'duplicate': selected.append(dict(source))
    if failure == 'binding':
        with service.store.transaction(True) as db:
            db.execute("UPDATE tournament_matches SET current_attempt_token='stale'")
    if failure == 'file':
        (service.store.directory / 'tournament-records-v1' / (result['record_sha256'] + '.json.gz')).write_bytes(b'bad')
    with pytest.raises(ValueError) as error:
        service.fit_rankings(selected, fit)
    assert 'wrong' not in str(error.value) and str(tmp_path) not in str(error.value)
    with service.store.transaction() as db:
        assert db.execute('SELECT count(*) FROM rating_comparisons').fetchone()[0] == 0


def test_combined_fit_partitions_engine_and_cas(tmp_path, monkeypatch):
    from boardcraft.play import ranking
    service = PlayService(tmp_path)
    a, _ = completed_source(service)
    b, _ = completed_source(service, engine='different-synthetic-engine')
    fit = service.tournament_schema()['rating_profiles'][0]['fit_spec']
    combined = service.fit_rankings([a, b], fit)
    assert len(combined['groups']) == 2
    assert {g['engine_version'] for g in combined['groups']} == {'1', 'different-synthetic-engine'}
    original = ranking.fit_ratings
    def raced(*args, **kwargs):
        # Acquiring this writer transaction proves numerical work has no writer lock.
        with service.store.transaction(True) as db:
            db.execute('UPDATE tournaments SET revision=revision+1 WHERE tournament_id=?', (a['tournament_id'],))
        return original(*args, **kwargs)
    monkeypatch.setattr(ranking, 'fit_ratings', raced)
    with pytest.raises(ValueError):
        service.fit_rankings([a], fit)
    with service.store.transaction() as db:
        assert db.execute('SELECT count(*) FROM rating_comparisons').fetchone()[0] == 1


def test_terminal_listed_summary_without_reading_engines(tmp_path, monkeypatch):
    from boardcraft.contracts import GameConfig
    service = PlayService(tmp_path, seed_factory=lambda: 1)
    for visibility in ('listed', 'invite'):
        made = service.create_game(GameConfig('dice_circuit', 'base', 1), ['human'], visibility=visibility)
        table = made['table_id']
        credential = service.join_game(table, 0)['credential']
        service.start_game(table, made['creator_credential'])
        assert all(item.get('table_id') != table for item in service.list_replays()['items'])
        while service.get_result(table)['result'] is None:
            actions = service.get_actions(table, credential)
            service.apply_action(table, credential, actions['decision_id'], actions['actions'][0]['id'])
        if visibility == 'listed': listed = table
    monkeypatch.setattr(service, 'module', lambda *_: pytest.fail('GET initialized engine'))
    monkeypatch.setattr(service, '_engine', lambda *_: pytest.fail('GET restored engine'))
    page = service.list_replays()
    assert page['total'] == 1 and page['items'][0]['table_id'] == listed
    assert page['items'][0]['result']['placements'] == [1]
    assert set(page['items'][0]) == {'source', 'table_id', 'game_id', 'variant', 'players', 'rules_version',
                                   'engine_version', 'participants', 'result', 'replay_path', 'played_at'}
    # The regular directory keeps its existing explicit allowlist.
    from boardcraft.server.directory import listing
    assert 'replay' not in encode(listing(service.store))


def test_http_explicit_fit_and_query_parity(tmp_path):
    service = PlayService(tmp_path)
    source, _ = completed_source(service)
    fit = service.tournament_schema()['rating_profiles'][0]['fit_spec']
    with TestClient(create_app(service, run_bots=False)) as client:
        result = client.post('/api/rankings/fit', json=dict(sources=[source], fit=fit))
        assert result.status_code == 200
        result = result.json()
        page = client.get('/api/rankings?limit=1&offset=0&players=2')
        assert page.status_code == 200
        assert page.json()['items'][0]['comparison_id'] == result['comparison_id']
        path = '/api/rankings/' + result['comparison_id']
        assert client.get(path).status_code == 422
        assert client.get(path + '?result_sha=bad').status_code in (400, 422)
        assert client.get(path + '?result_sha=' + '0' * 64).status_code == 409
        assert client.get(path + '?result_sha=' + result['result_sha']).json() == result
        assert client.get('/api/replays?limit=1&offset=0').status_code == 200


def test_explicit_source_count_and_row_limits_precede_decoding(tmp_path, monkeypatch):
    service = PlayService(tmp_path)
    source, _ = completed_source(service)
    fit = service.tournament_schema()['rating_profiles'][0]['fit_spec']
    with pytest.raises(ValueError):
        service.fit_rankings([dict(source, tournament_id=f'{i:032x}') for i in range(65)], fit)
    with service.store.transaction(True) as db:
        row = db.execute('SELECT * FROM tournament_matches').fetchone()
        db.executemany('INSERT INTO tournament_matches VALUES(?,?,?,?,?,?,?,?,?)',
                       [(row[0], f'{i:064x}', i + 1, row[3], row[4], row[5], row[6], row[7], row[8]) for i in range(10000)])
    from boardcraft.play.tournaments import TournamentStore
    monkeypatch.setattr(TournamentStore, 'verify_file', lambda *_: pytest.fail('Decoded before enforcing limit'))
    with pytest.raises(ValueError, match='10000'):
        service.fit_rankings([source], fit)
    with service.store.transaction() as db:
        assert db.execute('SELECT count(*) FROM rating_comparisons').fetchone()[0] == 0


@pytest.mark.parametrize('change', ['attempt', 'record', 'result', 'revision'])
def test_cas_detects_changed_snapshot_and_published_snapshot_unavailable(tmp_path, monkeypatch, change):
    from boardcraft.play import ranking
    service = PlayService(tmp_path)
    source, result = completed_source(service)
    fit = service.tournament_schema()['rating_profiles'][0]['fit_spec']
    first = service.fit_rankings([source], fit)
    original = ranking.fit_ratings
    def raced(*args, **kwargs):
        with service.store.transaction(True) as db:
            if change == 'attempt': db.execute("UPDATE tournament_matches SET current_attempt_token='stale'")
            if change == 'record': db.execute("UPDATE tournament_records SET state='gc_pending'")
            if change == 'revision': db.execute('UPDATE tournaments SET revision=1')
            if change == 'result':
                changed = dict(result, scores=[9, 1])
                raw = encode(changed)
                db.execute('UPDATE tournament_matches SET result_json=?,result_sha=?', (raw, digest(raw)))
        return original(*args, **kwargs)
    monkeypatch.setattr(ranking, 'fit_ratings', raced)
    with pytest.raises(ValueError): service.fit_rankings([source], fit)
    with service.store.transaction() as db:
        assert db.execute('SELECT count(*) FROM rating_comparisons').fetchone()[0] == 1
    if change != 'revision':
        assert service.list_rankings()['items'] == []
        with pytest.raises(ValueError): service.get_ranking(first['comparison_id'], first['result_sha'])


def test_corrupt_and_stale_table_summary_rejected_without_restore(tmp_path):
    service = PlayService(tmp_path)
    table = 'b' * 32
    summary = dict(table_id=table, game_id='dice_circuit', variant='base', players=1, status='completed', seats=[],
                   replay_revision=2, replay=dict(game_id='dice_circuit', variant='base', players=1,
                   rules_version='1', engine_version='1', participants=[dict(seat=0, kind='human', display_name='Player')],
                   result=dict(scores=[3], placements=[1])))
    with service.store.transaction(True) as db:
        raw = encode(summary)
        db.execute('INSERT INTO table_directory VALUES(?,?,?,?,?,?)', (table, 'dice_circuit', 'completed', 0, raw, digest(raw)))
        state = encode(dict(revision=3))
        db.execute('INSERT INTO tables VALUES(?,?,?,?)', (table, state, digest(state), 3))
    with pytest.raises(ValueError, match='stale'): service.list_replays()
    with service.store.transaction(True) as db:
        db.execute("UPDATE table_directory SET checksum='wrong'")
    with pytest.raises(ValueError, match='corrupt'): service.list_replays()


def test_library_relay_maps_all_four_tools_without_store(tmp_path):
    import io
    from urllib.parse import urlsplit, parse_qs
    from boardcraft.mcp.http import HttpPlayService
    class Response(io.BytesIO):
        headers = {'Content-Type': 'application/json'}
        status = 200
    class Opener:
        def __init__(self): self.calls = []
        def open(self, request, timeout):
            self.calls.append(request)
            return Response(b'{"ok":true}')
    opener = Opener()
    service = HttpPlayService('http://localhost:12345', opener=opener)
    assert not hasattr(service, 'store')
    service.list_rankings(game_id='dice_circuit', limit=2)
    service.list_replays(source='tournament', offset=1)
    service.get_ranking('c.' + 'a' * 32, 'b' * 64)
    body = dict(sources=[dict(tournament_id='a' * 32, spec_sha='b' * 64, revision=0, credential='secret')], fit={})
    service.fit_rankings(**body)
    assert [request.method for request in opener.calls] == ['GET', 'GET', 'GET', 'POST']
    assert [urlsplit(request.full_url).path for request in opener.calls] == ['/api/rankings', '/api/replays', '/api/rankings/c.' + 'a' * 32, '/api/rankings/fit']
    assert parse_qs(urlsplit(opener.calls[2].full_url).query) == {'result_sha': ['b' * 64]}
    assert json.loads(opener.calls[3].data) == body
    assert all(not request.has_header('Authorization') for request in opener.calls)


def test_combined_actual_rating_keys_and_new_results_do_not_merge_or_refit(tmp_path):
    service = PlayService(tmp_path)
    a, _ = completed_source(service)
    b, _ = completed_source(service, players=1)
    fit = service.tournament_schema()['rating_profiles'][0]['fit_spec']
    combined = service.fit_rankings([a, b], fit)
    assert {g['key']['players'] for g in combined['groups']} == {1, 2}
    assert len(service.list_rankings(players=1)['items'][0]['strata']) == 1
    with service.store.transaction(True) as db:
        row = db.execute('SELECT * FROM tournament_matches WHERE tournament_id=?', (a['tournament_id'],)).fetchone()
        # A later unrelated row is not part of the immutable pinned evidence.
        db.execute('INSERT INTO tournament_matches VALUES(?,?,?,?,?,?,?,?,?)',
                   (row[0], 'f' * 64, 1, row[3], row[4], 'completed', 'unpublished-later-attempt', None, None))
    assert service.get_ranking(combined['comparison_id'], combined['result_sha']) == combined


def test_combined_rejects_forged_result_key_before_fit(tmp_path):
    service = PlayService(tmp_path)
    source, result = completed_source(service)
    changed = dict(result, key={**result['key'], 'rules_version': 'forged'})
    raw = encode(changed)
    with service.store.transaction(True) as db:
        db.execute('UPDATE tournament_matches SET result_json=?,result_sha=?', (raw, digest(raw)))
    with pytest.raises(ValueError):
        service.fit_rankings([source], service.tournament_schema()['rating_profiles'][0]['fit_spec'])


def test_saved_fit_digest_checks_and_filters_before_paged_decode(tmp_path, monkeypatch):
    from boardcraft.server.storage import Store
    service = PlayService(tmp_path)
    for letter in 'abc': saved_fit(service, letter * 32)
    assert service.list_rankings(settings_digest='')['total'] == 3
    assert service.list_rankings(settings_digest='0' * 64)['total'] == 0
    assert service.list_rankings(rules_version='other')['total'] == 0
    assert service.list_rankings(variant='other')['total'] == 0
    calls, original = [], Store.checked
    def checked(row):
        calls.append(1)
        return original(row)
    monkeypatch.setattr(Store, 'checked', checked)
    assert len(service.list_rankings(limit=1)['items']) == 1
    assert len(calls) == 1
    with service.store.transaction(True) as db:
        db.execute("UPDATE tournament_ratings SET key_sha=? WHERE tournament_id=?", ('0' * 64, 'a' * 32))
    with pytest.raises(ValueError, match='corrupt'): service.list_rankings()


def test_snapshot_corruption_and_replay_binding_errors_are_public(tmp_path):
    service = PlayService(tmp_path)
    source, _ = completed_source(service)
    fit = service.tournament_schema()['rating_profiles'][0]['fit_spec']
    combined = service.fit_rankings([source], fit)
    with service.store.transaction(True) as db:
        db.execute("UPDATE rating_comparisons SET checksum='bad'")
    with TestClient(create_app(service, run_bots=False)) as client:
        response = client.get('/api/rankings/' + combined['comparison_id'] + '?result_sha=' + combined['result_sha'])
        assert response.status_code == 400 and 'corrupt' in response.json()['detail']
        source['credential'] = 'private-secret-never-returned'
        response = client.post('/api/rankings/fit', json=dict(sources=[source], fit=fit))
        assert response.status_code == 403
        assert source['credential'] not in response.text and str(tmp_path) not in response.text
        with service.store.transaction(True) as db:
            db.execute("UPDATE tournament_matches SET result_json=NULL,result_sha=NULL")
        response = client.get('/api/replays')
        assert response.status_code == 400
        assert str(tmp_path) not in response.text


def test_four_mcp_tools_over_http_relay_use_identical_saved_dtos(tmp_path):
    import io
    from urllib.error import HTTPError
    from boardcraft.mcp.http import HttpPlayService
    from boardcraft.mcp.server import create_server
    service = PlayService(tmp_path)
    source, _ = completed_source(service)
    fit = service.tournament_schema()['rating_profiles'][0]['fit_spec']
    with TestClient(create_app(service, run_bots=False)) as http:
        class Response(io.BytesIO):
            status = 200
            headers = {'Content-Type': 'application/json'}
        class Opener:
            def open(self, request, timeout):
                response = http.request(request.method, request.full_url.replace('http://play.test', ''),
                                        content=request.data, headers=dict(request.header_items()))
                if response.status_code != 200:
                    raise HTTPError(request.full_url, response.status_code, 'error', {}, io.BytesIO(response.content))
                return Response(response.content)
        relay = HttpPlayService('http://play.test', opener=Opener())
        async def check():
            async with Client(create_server(relay)) as client:
                result = await client.call_tool('fit_rankings', dict(sources=[source], fit=fit))
                assert not result.is_error
                combined = json.loads(result.content[0].text)
                pin = {key: combined[key] for key in ('comparison_id', 'result_sha')}
                result = await client.call_tool('get_ranking', pin)
                assert not result.is_error and json.loads(result.content[0].text) == combined
                for operation in ('list_rankings', 'list_replays'):
                    result = await client.call_tool(operation, {})
                    assert not result.is_error
                    assert json.loads(result.content[0].text) == getattr(service, operation)()
        asyncio.run(check())


def test_empty_sources_do_not_publish_combined_comparison(tmp_path):
    service = PlayService(tmp_path)
    saved, fit = saved_fit(service)
    with service.store.transaction() as db:
        sha = db.execute('SELECT spec_sha FROM tournaments').fetchone()[0]
    with TestClient(create_app(service, run_bots=False)) as client:
        response = client.post('/api/rankings/fit', json=dict(sources=[dict(
            tournament_id='a' * 32, spec_sha=sha, revision=0, credential='secret')], fit=fit))
        assert response.status_code == 400
        assert response.json()['detail'] == 'Selected sources have no completed results'
    with service.store.transaction() as db:
        assert db.execute('SELECT count(*) FROM rating_comparisons').fetchone()[0] == 0
    # The pre-existing independently saved empty fit is still available.
    assert service.list_rankings()['total'] == 1
