"""Bounded, instance-local discovery and explicitly fitted immutable comparisons."""
import json
import sqlite3
from functools import wraps
import re
import uuid
from collections import defaultdict

from pydantic import ValidationError
from .storage import Store, digest, encode
from .views import Conflict
from .errors import ServiceError
from .library_schemas import DeleteReplaysInput, FitRankingsInput, RankingInput, RankingListInput, ReplayListInput


def _public_operation(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except ValidationError:
            raise ValueError('Invalid library request') from None
        except (KeyError, TypeError, AttributeError, IndexError, sqlite3.DatabaseError):
            raise ServiceError('Stored library data is corrupt or unavailable', code='stored_data_unavailable') from None
    return guarded


def _tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _tstore(store):
    # Reading an existing store must not run its creating constructor or controller.
    from boardcraft.play.tournaments import TournamentStore
    result = object.__new__(TournamentStore)
    result.store = store
    result.root = store.directory / 'tournament-records-v1'
    return result


def _managed_tstore(service):
    result = _tstore(service.store)
    result.open_management = getattr(service, 'open_management', False)
    return result


def _active(row):
    if row['status'] in ('deleting', 'deleted', 'cleanup_unconfirmed'):
        raise ValueError('Comparison source unavailable')


def _page(items, query):
    return dict(items=items[query.offset:query.offset + query.limit], total=len(items),
                offset=query.offset, limit=query.limit)


def _strata(group):
    return {key: group[key] for key in ('key', 'engine_version', 'observed_games', 'observed_pairs')} | {
        'component_count': len(group['components'])}


def _group(group, key_sha=None, fit_sha=None):
    from boardcraft.play.ranking import RatingKey, RatingFitSpec
    key = RatingKey(**group['key']).data()
    fit = RatingFitSpec(**group['fit_spec']).data()
    if (key_sha is not None and digest(encode(key)) != key_sha
            or fit_sha is not None and digest(encode(fit)) != fit_sha):
        raise ValueError('Stored comparison is corrupt')
    _strata(group)
    return group


def _matches(group, query):
    return all(value is None or group['key'].get(key, '' if key == 'settings_digest' else None) == value
               for key, value in query.model_dump().items()
               if key in ('game_id', 'rules_version', 'variant', 'players', 'settings_digest'))


def _manifest_result(result, result_sha):
    keys = ('match_id', 'attempt_token', 'record_sha256', 'record_size', 'key',
            'engine_version', 'identities', 'scores', 'placements')
    return {key: result[key] for key in keys} | {'result_sha': result_sha}


def _snapshot(db, row, ts):
    manifest = Store.checked((row['manifest_json'], row['manifest_sha']))
    fit = Store.checked((row['fit_json'], row['fit_sha']))
    payload = Store.checked((row['payload'], row['checksum']))
    if (payload['manifest'] != manifest or payload['manifest_sha'] != row['manifest_sha']
            or payload['fit_spec'] != fit or payload['comparison_id'] != 'c.' + row['comparison_id']):
        raise ValueError('Stored comparison is corrupt')
    for source in manifest['sources']:
        current = ts.load(db, source['tournament_id'])
        _active(current)
        if current['spec_sha'] != source['spec_sha']:
            raise ValueError('Comparison source unavailable')
        # Later new results do not alter the pinned sample. Every pinned binding must remain current.
        match_ids = [result['match_id'] for result in source['results']]
        shas = dict(db.execute("SELECT match_id,result_sha FROM tournament_matches WHERE tournament_id=? AND match_id IN (SELECT value FROM json_each(?))", (source['tournament_id'], encode(match_ids))))
        available = {r['match_id']: _manifest_result(r, shas[r['match_id']]) for r in ts.rating_results(db, source['tournament_id'], match_ids=match_ids)}
        for result in source['results']:
            if available.get(result['match_id']) != result:
                raise ValueError('Comparison source unavailable')
    for group in payload['groups']:
        _group(group, fit_sha=row['fit_sha'])
    return payload


@_public_operation
def list_rankings(service, **filters):
    query = RankingListInput.model_validate(filters)
    ts = _tstore(service.store)
    items = []
    with service.store.transaction() as db:
        db.row_factory = sqlite3.Row
        tables = _tables(db)
        if 'tournament_ratings' not in tables:
            return _page([], query)
        values = []
        clauses = ["t.status NOT IN ('deleting','deleted','cleanup_unconfirmed')"]
        if query.tournament_id:
            clauses.append('r.tournament_id=?')
            values.append(query.tournament_id)
        def key_filters(expression):
            expressions, arguments = [], []
            for name in ('game_id', 'rules_version', 'variant', 'players', 'settings_digest'):
                value = getattr(query, name)
                if value is not None:
                    expressions.append("coalesce(json_extract(" + expression + ",'$.key." + name + "'),'')=?")
                    arguments.append(value)
            return expressions, arguments
        filters, args = key_filters('r.payload')
        clauses.extend(filters)
        values.extend(args)
        selects = ["SELECT 'tournament_fit' AS source,'t.'||r.tournament_id||'.'||r.key_sha||'.'||r.fit_spec_sha AS comparison_id FROM tournament_ratings r JOIN tournaments t ON t.tournament_id=r.tournament_id WHERE " + ' AND '.join(clauses)]
        if 'rating_comparisons' in tables:
            # Availability uses only the pinned rows, current attempt, and ready binding.
            # SQL filters/counts/pages before any large immutable manifest is decoded.
            clauses = ["""NOT EXISTS (
                SELECT 1 FROM json_each(c.manifest_json,'$.sources') s
                LEFT JOIN tournaments t ON t.tournament_id=json_extract(s.value,'$.tournament_id')
                WHERE t.tournament_id IS NULL OR t.status IN ('deleted','deleting','cleanup_unconfirmed')
                    OR t.spec_sha != json_extract(s.value,'$.spec_sha')
                    OR EXISTS (
                        SELECT 1 FROM json_each(s.value,'$.results') r
                        LEFT JOIN tournament_matches m ON m.tournament_id=t.tournament_id
                            AND m.match_id=json_extract(r.value,'$.match_id')
                        LEFT JOIN tournament_attempts a ON a.tournament_id=m.tournament_id
                            AND a.match_id=m.match_id AND a.token=m.current_attempt_token AND a.status='completed'
                        LEFT JOIN tournament_record_refs f ON f.tournament_id=a.tournament_id
                            AND f.match_id=a.match_id AND f.attempt_token=a.token AND f.sha256=a.record_sha AND f.role='completed'
                        LEFT JOIN tournament_records o ON o.sha256=f.sha256 AND o.state='ready'
                        WHERE m.match_id IS NULL OR m.status!='completed' OR o.sha256 IS NULL
                            OR m.result_sha IS NOT json_extract(r.value,'$.result_sha')
                            OR a.token != json_extract(r.value,'$.attempt_token')
                            OR o.sha256 != json_extract(r.value,'$.record_sha256')
                            OR o.size != json_extract(r.value,'$.record_size')
                            OR o.relpath != o.sha256||'.json.gz'
                    ))"""]
            if query.tournament_id:
                clauses.append("EXISTS(SELECT 1 FROM json_each(c.manifest_json,'$.sources') s WHERE json_extract(s.value,'$.tournament_id')=?)")
                values.append(query.tournament_id)
            filters, args = key_filters('g.value')
            clauses.append("EXISTS(SELECT 1 FROM json_each(c.payload,'$.groups') g" +
                           (' WHERE ' + ' AND '.join(filters) if filters else '') + ')')
            values.extend(args)
            selects.append("SELECT 'combined_fit' AS source,'c.'||c.comparison_id AS comparison_id FROM rating_comparisons c WHERE " + ' AND '.join(clauses))
        union = ' UNION ALL '.join(selects)
        total = db.execute('SELECT count(*) FROM (' + union + ')', values).fetchone()[0]
        selected = db.execute(union + ' ORDER BY source,comparison_id LIMIT ? OFFSET ?',
                              [*values, query.limit, query.offset]).fetchall()
        for candidate in selected:
            comparison_id, source = candidate['comparison_id'], candidate['source']
            parts = comparison_id.split('.')
            if source == 'tournament_fit':
                row = db.execute('SELECT * FROM tournament_ratings WHERE tournament_id=? AND key_sha=? AND fit_spec_sha=?', parts[1:]).fetchone()
                group = _group(Store.checked((row['payload'], row['checksum'])), parts[2], parts[3])
                saved = ts.load(db, parts[1])
                name = saved['spec']['name']
                items.append(dict(comparison_id=comparison_id, source=source, name=name,
                                  sources=[dict(tournament_id=parts[1], tournament_name=name)],
                                  result_sha=row['checksum'], fit_spec=group['fit_spec'], strata=[_strata(group)]))
            else:
                row = db.execute('SELECT * FROM rating_comparisons WHERE comparison_id=?', (parts[1],)).fetchone()
                payload = _snapshot(db, row, ts)
                sources = payload['manifest']['sources']
                groups = [g for g in payload['groups'] if _matches(g, query)]
                items.append(dict(comparison_id=comparison_id, source=source, name='Combined comparison',
                                  sources=[dict(tournament_id=s['tournament_id'], tournament_name=s['tournament_name']) for s in sources],
                                  manifest_sha=row['manifest_sha'], result_sha=row['checksum'],
                                  fit_spec=payload['fit_spec'], strata=[_strata(g) for g in groups]))
    return dict(items=items, total=total, offset=query.offset, limit=query.limit)


@_public_operation
def get_ranking(service, comparison_id, result_sha):
    query = RankingInput(comparison_id=comparison_id, result_sha=result_sha)
    ts = _tstore(service.store)
    with service.store.transaction() as db:
        db.row_factory = sqlite3.Row
        tables = _tables(db)
        if 'tournament_ratings' not in tables:
            raise ValueError('Comparison unavailable')
        parts = query.comparison_id.split('.')
        if parts[0] == 'c':
            row = None if 'rating_comparisons' not in tables else db.execute(
                'SELECT * FROM rating_comparisons WHERE comparison_id=?', (parts[1],)).fetchone()
            if row is None:
                raise ValueError('Comparison unavailable')
            payload = _snapshot(db, row, ts)
        else:
            _active(ts.load(db, parts[1]))
            row = db.execute('SELECT * FROM tournament_ratings WHERE tournament_id=? AND key_sha=? AND fit_spec_sha=?', parts[1:]).fetchone()
            if row is None:
                raise ValueError('Comparison unavailable')
            group = _group(Store.checked((row['payload'], row['checksum'])), parts[2], parts[3])
            payload = dict(comparison_id=comparison_id, source='tournament_fit', fit_spec=group['fit_spec'], groups=[group])
        if row['checksum'] != result_sha:
            raise Conflict('Comparison result changed; refresh the library')
        return payload | {'result_sha': row['checksum']}


@_public_operation
def fit_rankings(service, sources, fit):
    from boardcraft.play.ranking import RatingFitSpec, fit_ratings
    request = FitRankingsInput(sources=sources, fit=fit)
    ids = [s.tournament_id for s in request.sources]
    if len(set(ids)) != len(ids):
        raise ValueError('Choose unique comparison sources')
    ts = _managed_tstore(service)
    settings = RatingFitSpec(**request.fit.model_dump())
    ordered = sorted(request.sources, key=lambda source: source.tournament_id)
    def capture(db):
        manifest_sources = []
        captured = {}
        total = 0
        for source in ordered:
            row = ts.load(db, source.tournament_id)
            ts.authorize(row, source.credential, source.revision, source.spec_sha)
            _active(row)
            total += db.execute("SELECT count(*) FROM tournament_matches WHERE tournament_id=? AND status='completed'", (source.tournament_id,)).fetchone()[0]
            if total > 10000:
                raise ValueError('A combined fit supports at most 10000 completed result rows')
            results = ts.rating_results(db, source.tournament_id)
            captured[source.tournament_id] = (row, results)
            shas = dict(db.execute("SELECT match_id,result_sha FROM tournament_matches WHERE tournament_id=? AND status='completed'", (source.tournament_id,)))
            manifest_sources.append(dict(tournament_id=source.tournament_id, tournament_name=row['spec']['name'],
                                         spec_sha=source.spec_sha, revision=source.revision, results=[_manifest_result(r, shas[r['match_id']]) for r in results]))
        return dict(version=1, sources=manifest_sources), captured
    with service.store.transaction() as db:
        if 'tournaments' not in _tables(db):
            raise ValueError('Comparison source unavailable')
        manifest, captured = capture(db)
    if not any(source['results'] for source in manifest['sources']):
        raise ServiceError('Selected sources have no completed results', code='no_completed_results', argument='sources')
    groups = defaultdict(list)
    # Verification and numerical work occur outside the write lock.
    for source in manifest['sources']:
        row, results = captured[source['tournament_id']]
        results = service.tournaments().verified_rating_results(row, results)
        for result in results:
            groups[(encode(result['key']), result['engine_version'])].append(result)
    from boardcraft.play.ranking import RatingKey
    fitted = [fit_ratings(rows, RatingKey(**json.loads(key)), fit_spec=settings)
              for (key, engine), rows in sorted(groups.items())]
    id = uuid.uuid4().hex
    manifest_raw, fit_raw = encode(manifest), encode(settings.data())
    payload = dict(comparison_id='c.' + id, source='combined_fit', manifest=manifest,
                   manifest_sha=digest(manifest_raw), fit_spec=settings.data(), groups=fitted)
    raw = encode(payload)
    with service.store.transaction(True) as db:
        try:
            unchanged = capture(db)[0] == manifest
        except (ValueError, KeyError, TypeError, AttributeError):
            raise Conflict('Completed result snapshot changed') from None
        if not unchanged:
            raise Conflict('Completed result snapshot changed')
        db.execute('INSERT INTO rating_comparisons VALUES(?,?,?,?,?,?,?)',
                   (id, manifest_raw, digest(manifest_raw), fit_raw, digest(fit_raw), raw, digest(raw)))
    return payload | {'result_sha': digest(raw)}


REMOVALS = ("CREATE TABLE IF NOT EXISTS replay_removals(source TEXT NOT NULL,source_id TEXT NOT NULL,match_id TEXT NOT NULL,"
            "removed_at REAL NOT NULL,PRIMARY KEY(source,source_id,match_id))")


def _like(text):
    """A case-insensitive LIKE pattern for a part of a name (wildcards typed by the user are literal)."""
    text = (text or '').strip().lower()
    return '%' + text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%' if text else None


def removed_matches(db):
    """(tournament_id, match_id) of the tournament games deleted from the library (the rankings leave them out)."""
    if 'replay_removals' not in _tables(db):
        return set()
    return {tuple(row) for row in db.execute("SELECT source_id,match_id FROM replay_removals WHERE source='tournament'")}


@_public_operation
def list_replays(service, **filters):
    """One page of the replay library, newest first, filtered by the server: game, a part of a player's name (only
    their wins with ``won``) and a part of the tournament name. Only stored rows are read (no game is replayed)."""
    query = ReplayListInput.model_validate(filters)
    if query.source == 'table' and query.tournament_id is not None:
        raise ValueError('A table replay cannot have a tournament source')
    who, tour = _like(query.q), _like(query.tournament)
    ts = _tstore(service.store)
    items = []
    with service.store.transaction() as db:
        db.row_factory = sqlite3.Row
        tables = _tables(db)
        removals = 'replay_removals' in tables
        selects, values = [], []
        if query.source != 'tournament' and query.tournament_id is None and tour is None:
            sql = ("SELECT 'table' AS source,d.table_id AS source_id,'' AS match_id,0 AS ordinal,d.created_at AS played,"
                   "d.rowid AS saved FROM table_directory d WHERE d.status='completed' AND json_type(d.payload,'$.replay')='object'")
            if removals:
                sql += " AND NOT EXISTS(SELECT 1 FROM replay_removals x WHERE x.source='table' AND x.source_id=d.table_id)"
            if query.game_id:
                sql += ' AND d.game_id=?'
                values.append(query.game_id)
            if who:
                sql += (" AND EXISTS(SELECT 1 FROM json_each(d.payload,'$.replay.participants') p"
                        " WHERE lower(coalesce(json_extract(p.value,'$.display_name'),'')) LIKE ? ESCAPE '\\'")
                values.append(who)
                if query.won:
                    sql += " AND json_extract(d.payload,'$.replay.result.placements['||p.key||']')=1"
                sql += ')'
            selects.append(sql)
        if query.source != 'table' and 'tournaments' in tables:
            created = 'tournament_created' in tables
            sql = ("SELECT 'tournament' AS source,m.tournament_id AS source_id,m.match_id,m.ordinal,"
                   + ("coalesce(c.created_at,0)" if created else "0") + " AS played,t.rowid AS saved "
                   "FROM tournament_matches m JOIN tournaments t ON t.tournament_id=m.tournament_id "
                   + ("LEFT JOIN tournament_created c ON c.tournament_id=m.tournament_id " if created else "")
                   + "WHERE m.status='completed' AND t.status NOT IN ('deleted','deleting','cleanup_unconfirmed')")
            if removals:
                sql += (" AND NOT EXISTS(SELECT 1 FROM replay_removals x WHERE x.source='tournament'"
                        " AND x.source_id=m.tournament_id AND x.match_id=m.match_id)")
            if query.tournament_id:
                sql += ' AND m.tournament_id=?'
                values.append(query.tournament_id)
            if query.game_id:
                sql += " AND json_extract(t.spec_json,'$.config.game_id')=?"
                values.append(query.game_id)
            if tour:
                sql += " AND lower(json_extract(t.spec_json,'$.name')) LIKE ? ESCAPE '\\'"
                values.append(tour)
            if who:
                if 'tournament_labels' not in tables:
                    sql += ' AND 0'
                else:
                    # The names matching the search are found among the tournament's saved names; a game is then
                    # matched by the identity text in its result (the JSON of a game is parsed only for "only wins").
                    sql += (" AND EXISTS(SELECT 1 FROM tournament_labels l JOIN json_each(l.labels_json) n"
                            " WHERE l.tournament_id=m.tournament_id AND lower(n.value) LIKE ? ESCAPE '\\'"
                            " AND instr(m.result_json,n.key)>0")
                    values.append(who)
                    if query.won:
                        sql += (" AND EXISTS(SELECT 1 FROM json_each(m.result_json,'$.identities') i WHERE i.value=n.key"
                                " AND json_extract(m.result_json,'$.placements['||i.key||']')=1)")
                    sql += ')'
            selects.append(sql)
        union = ' UNION ALL '.join(selects)
        total = db.execute('SELECT count(*) FROM (' + union + ')', values).fetchone()[0] if selects else 0
        # Newest first: tables by their creation time, tournament games by their tournament's (later games first).
        candidates = db.execute(union + ' ORDER BY played DESC,saved DESC,ordinal DESC LIMIT ? OFFSET ?',
                                [*values, query.limit, query.offset]).fetchall() if total else []
        tournaments, labels = {}, {}
        from .directory import checked_replay_summary
        from .leaderboard import _fallback_name, _kind
        for kind, source_id, match_id, ordinal, played, _ in candidates:
            if kind == 'table':
                row = db.execute('SELECT payload,checksum FROM table_directory WHERE table_id=?', (source_id,)).fetchone()
                summary = Store.checked(row)
                replay = summary['replay']
                checked_replay_summary(summary, replay)
                current = db.execute('SELECT revision FROM tables WHERE table_id=?', (source_id,)).fetchone()
                if current is None or current[0] != summary['replay_revision']:
                    raise ValueError('Stored replay summary is stale')
                items.append(dict(source='table', table_id=source_id, **replay, played_at=float(played),
                                  replay_path='/api/tables/' + source_id + '/replay'))
            else:
                if source_id not in tournaments:
                    tournaments[source_id] = ts.load(db, source_id)
                    saved = (db.execute('SELECT labels_json FROM tournament_labels WHERE tournament_id=?', (source_id,)).fetchone()
                             if 'tournament_labels' in tables else None)
                    labels[source_id] = json.loads(saved[0]) if saved else {}
                spec = tournaments[source_id]['spec']
                # Binding validation uses persisted ready-result metadata; no file decoding.
                results = ts.rating_results(db, source_id, match_id=match_id)
                if len(results) != 1:
                    raise ValueError('Completed replay unavailable')
                result = results[0]
                names = labels[source_id]
                # The saved name of each player (a bot or a model), never the kind of its reference.
                participants = [dict(seat=i, kind='model' if _kind(ref) == 'model' else 'bot',
                                     display_name=names.get(result['identities'][i]) or _fallback_name(ref),
                                     identity=result['identities'][i]) for i, ref in enumerate(result['participants'])]
                items.append(dict(source='tournament', tournament_id=source_id, tournament_name=spec['name'],
                                  match_id=match_id, ordinal=ordinal,
                                  **{key: result['key'][key] for key in ('game_id', 'variant', 'players', 'rules_version')},
                                  engine_version=result['engine_version'], participants=participants,
                                  result={key: result[key] for key in ('scores', 'placements')}, played_at=float(played),
                                  replay_path='/api/tournaments/' + source_id + '/matches/' + match_id + '/replay'))
    return dict(items=items, total=total, offset=query.offset, limit=query.limit, **service.management())


def delete_replays(service, replays):
    """Marks replays as deleted from the library (a server with open management only). Returns how many were newly
    deleted and whether a tournament game left the rankings."""
    import time
    from .library_schemas import DeleteReplaysInput
    from .views import AccessDenied
    try:
        request = DeleteReplaysInput.model_validate(dict(replays=replays))
    except ValidationError:
        raise ValueError('Choose the replays to delete') from None
    if not getattr(service, 'open_management', False):
        raise AccessDenied('Only a private server deletes replays')
    keys = []
    for ref in request.replays:
        if ref.source == 'table':
            if ref.table_id is None or ref.tournament_id is not None or ref.match_id is not None:
                raise ValueError('A table replay names its table')
            keys.append(('table', ref.table_id, ''))
        else:
            if ref.tournament_id is None or ref.match_id is None or ref.table_id is not None:
                raise ValueError('A tournament replay names its tournament and game')
            keys.append(('tournament', ref.tournament_id, ref.match_id))
    deleted, rankings = 0, False
    now = time.time()
    with service.store.transaction(True) as db:
        db.execute(REMOVALS)
        held = 'tournament_matches' in _tables(db)
        for key in dict.fromkeys(keys):
            if db.execute('SELECT 1 FROM replay_removals WHERE source=? AND source_id=? AND match_id=?', key).fetchone():
                continue
            if key[0] == 'table':
                found = db.execute("SELECT 1 FROM table_directory WHERE table_id=? AND status='completed'", key[1:2]).fetchone()
            else:
                found = held and db.execute("SELECT 1 FROM tournament_matches WHERE tournament_id=? AND match_id=? "
                                            "AND status='completed'", key[1:]).fetchone()
            if not found:
                continue
            db.execute('INSERT INTO replay_removals VALUES(?,?,?,?)', (*key, now))
            deleted += 1
            rankings = rankings or key[0] == 'tournament'
    return dict(deleted=deleted, rankings=rankings)


def register_library_routes(app, service):
    from fastapi import Request
    def query_input(request, model):
        values = {}
        for key, value in request.query_params.multi_items():
            if key in values:
                raise ValueError('Duplicate library query field')
            if key == 'won':
                if value not in ('0', '1', 'true', 'false'):
                    raise ValueError('Invalid library query')
                value = value in ('1', 'true')
            elif key in ('offset', 'limit', 'players'):
                if not re.fullmatch('[0-9]+', value):
                    raise ValueError('Invalid library integer query')
                value = int(value)
            values[key] = value
        try:
            return model.model_validate(values).model_dump()
        except ValueError:
            raise ValueError('Invalid library query') from None
    @app.get('/api/rankings')
    def rankings(request: Request):
        return service.list_rankings(**query_input(request, RankingListInput))
    @app.get('/api/rankings/global')
    def global_ranking(game_id: str, players: int):
        # The stored global ranking of one game and table size; computed by the server, never on a read.
        return service.global_ranking(game_id, players)
    @app.post('/api/rankings/fit')
    def fit(body: FitRankingsInput):
        return service.fit_rankings(**body.model_dump())
    @app.get('/api/rankings/{comparison_id}')
    def ranking(comparison_id: str, result_sha: str):
        try:
            query = RankingInput(comparison_id=comparison_id, result_sha=result_sha)
        except ValueError:
            raise ValueError('Invalid comparison pin') from None
        return service.get_ranking(**query.model_dump())
    @app.get('/api/replays')
    def replays(request: Request):
        return service.list_replays(**query_input(request, ReplayListInput))
    @app.post('/api/replays/delete')
    def delete_replays(body: DeleteReplaysInput):
        return service.delete_replays([item.model_dump() for item in body.replays])
