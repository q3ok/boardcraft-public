"""Safe opt-in summaries, maintained within the owning table transaction."""
from dataclasses import dataclass
import math
from .storage import digest, encode, Store
from .views import seats_view


def validate_filters(*, game_id=None, status=None, offset=0, limit=25):
    if game_id is not None and (not isinstance(game_id, str) or not 1 <= len(game_id) <= 80):
        raise ValueError('Invalid game module filter')
    if status is not None and status not in ('lobby', 'playing', 'completed'):
        raise ValueError('Invalid table status filter')
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Invalid table page bounds')


def summary(table, state):
    config = state['config']
    return {'table_id': table, 'game_id': config['game_id'], 'variant': config['variant'],
            'players': config['players'], 'status': state['status'],
            'seats': [{key: item[key] for key in ('seat', 'kind', 'occupied', 'display_name', 'token') if key in item}
                      for item in seats_view(state)],
            **({'hidden': True} if state.get('hidden') else {})}


@dataclass(frozen=True)
class PreparedSummary:
    game_id: str
    status: str
    payload: str
    checksum: str


def table_round(engine, module=None):
    """The round a game is in and its round count (None without a limit), for the table list; None if unknown."""
    try:
        hook = getattr(module, 'table_round', None)
        found = hook(engine) if hook else None
    except Exception:  # noqa: BLE001 -- the round is an optional list detail
        return None
    if not found or type(found[0]) is not int:
        return None
    return {'round': found[0], 'rounds': found[1] if type(found[1]) is int else None}


def prepare_summary(table, state, *, engine=None, rules_version=None, engine_version=None, previous=None):
    value = summary(table, state)
    progress = None
    if engine is not None:
        from boardcraft.registry import get_game
        try:
            module = get_game(state['config']['game_id'])
        except KeyError:  # a game served only by this service (tests) has no listed round
            module = None
        progress = table_round(engine, module)
    if progress is None and previous:
        progress = {k: previous[k] for k in ('round', 'rounds') if k in previous} or None
    if progress and state['status'] != 'lobby':
        value.update(progress)
    if state['status'] == 'completed' and engine is not None:
        result = engine.result()
        if result is None:
            raise ValueError('Completed table result unavailable')
        value['replay'] = {**{key: state['config'][key] for key in ('game_id', 'variant', 'players')},
                           'rules_version': rules_version, 'engine_version': engine_version,
                           'participants': [dict(seat=item['seat'], display_name=item['display_name'],
                                                 kind=item['kind'] if item['kind'] in ('human', 'agent', 'model') else 'bot')
                                            for item in value['seats']],
                           'result': {'scores': list(result.scores), 'placements': list(result.placements)}}
        value['replay_revision'] = state['revision']
        checked_replay_summary(value, value['replay'])
    payload = encode(value)
    return PreparedSummary(value['game_id'], value['status'], payload, digest(payload))


def insert(db, table, state, created_at):
    prepared = prepare_summary(table, state)
    db.execute('INSERT INTO table_directory VALUES (?, ?, ?, ?, ?, ?)',
               (table, prepared.game_id, prepared.status, created_at, prepared.payload, prepared.checksum))


def update(db, table, state):
    # The listed round survives a summary rewrite without an engine (SQLite reads the two fields; no JSON decoding here).
    row = db.execute("SELECT json_extract(payload,'$.round'), json_extract(payload,'$.rounds') FROM table_directory WHERE table_id=?",
                     (table,)).fetchone()
    previous = {'round': row[0], 'rounds': row[1]} if row is not None and type(row[0]) is int else None
    write_summary(db, table, prepare_summary(table, state, previous=previous))


def prepare_round(store, table, progress):
    """A listed summary with a new round, prepared before the writer transaction; None when unchanged or unlisted."""
    with store.transaction() as db:
        row = db.execute('SELECT payload, checksum FROM table_directory WHERE table_id=?', (table,)).fetchone()
    if row is None:
        return None
    value = Store.checked(row)
    if value.get('round') == progress['round'] and value.get('rounds') == progress['rounds']:
        return None
    payload = encode({**value, **progress})
    return payload, digest(payload), row[1]


def write_round(db, table, prepared):
    """Writes a prepared round unless the summary changed meanwhile (the next round change writes it again)."""
    payload, checksum, previous = prepared
    db.execute('UPDATE table_directory SET payload=?, checksum=? WHERE table_id=? AND checksum=?', (payload, checksum, table, previous))


def write_summary(db, table, prepared):
    db.execute('UPDATE table_directory SET game_id=?, status=?, payload=?, checksum=? WHERE table_id=?',
               (prepared.game_id, prepared.status, prepared.payload, prepared.checksum, table))


def listing(store, *, game_id=None, status=None, offset=0, limit=25):
    clauses, values = [], []
    for key, value in (('game_id', game_id), ('status', status)):
        if value is not None:
            clauses.append(key + '=?')
            values.append(value)
    # A deleted table leaves the list; its replay (when completed) stays in the replay library.
    clauses.append("json_extract(payload,'$.hidden') IS NULL")
    where = ' WHERE ' + ' AND '.join(clauses)
    with store.transaction() as db:
        total = db.execute('SELECT count(*) FROM table_directory' + where, values).fetchone()[0]
        rows = [] if offset >= total else db.execute('SELECT payload, checksum, rowid FROM table_directory' + where +
                          ' ORDER BY created_at, table_id LIMIT ? OFFSET ?', [*values, limit, offset]).fetchall()
    # The number is the table's place in this server's directory (it never changes and is never reused).
    return {'items': [{**public_summary(Store.checked(row[:2])), 'number': row[2]} for row in rows],
            'total': total, 'offset': offset, 'limit': limit}


def public_summary(value):
    """The listed summary; a completed table also names its winning seat (placement 1 of its result)."""
    item = {key: part for key, part in value.items() if key not in ('replay', 'replay_revision', 'hidden')}
    placements = (value.get('replay') or {}).get('result', {}).get('placements')
    if value.get('status') == 'completed' and placements and 1 in placements:
        item['winner'] = placements.index(1)
    return item


def checked_replay_summary(summary, replay):
    expected = {'game_id', 'variant', 'players', 'rules_version', 'engine_version', 'participants', 'result'}
    if (type(replay) is not dict or set(replay) != expected or summary['status'] != 'completed'
            or any(replay[key] != summary[key] for key in ('game_id', 'variant', 'players'))
            or any(type(replay[key]) is not str or not replay[key] for key in ('rules_version', 'engine_version'))):
        raise ValueError('Stored replay summary is corrupt')
    n = replay['players']
    if (type(n) is not int or n < 1 or type(summary.get('replay_revision')) is not int
            or len(replay['participants']) != n or set(replay['result']) != {'scores', 'placements'}):
        raise ValueError('Stored replay summary is corrupt')
    for i, participant in enumerate(replay['participants']):
        if (set(participant) != {'seat', 'kind', 'display_name'} or participant['seat'] != i
                or participant['kind'] not in ('human', 'agent', 'builtin', 'model', 'bot')
                or participant['display_name'] is not None and type(participant['display_name']) is not str):
            raise ValueError('Stored replay summary is corrupt')
    scores, placements = replay['result']['scores'], replay['result']['placements']
    if (len(scores) != n or len(placements) != n
            or any(type(score) not in (int, float) or not math.isfinite(score) for score in scores)
            or any(type(place) is not int or not 1 <= place <= n for place in placements)):
        raise ValueError('Stored replay summary is corrupt')
    return replay
