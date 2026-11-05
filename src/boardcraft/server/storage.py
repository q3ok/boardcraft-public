"""Transactional local SQLite state and append-only perspective journals."""
from contextlib import closing, contextmanager, nullcontext
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
from weakref import WeakValueDictionary
from boardcraft.contracts import BotReference


@dataclass(frozen=True)
class TableSeat:
    kind: str
    bot_ref: BotReference | None
    display_name: str

_writer_locks = WeakValueDictionary()
_writer_locks_guard = threading.Lock()


def writer_lock(path):
    with _writer_locks_guard:
        lock = _writer_locks.get(path)
        if lock is None:
            lock = threading.Lock()
            _writer_locks[path] = lock
        return lock


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def default_data_dir():
    root = os.environ.get('LOCALAPPDATA') or os.environ.get('XDG_DATA_HOME')
    return Path(root) / 'Boardcraft' if root else Path.home() / '.local' / 'share' / 'boardcraft'


def safe_data_dir(value):
    path = Path(value).expanduser().resolve()
    package = Path(__file__).resolve().parents[1]
    forbidden = [package]
    for parent in package.parents:
        if (parent / 'pyproject.toml').is_file() and (parent / 'src' / 'boardcraft').is_dir():
            forbidden.append(parent)
            break
    if any(path == root or root in path.parents for root in forbidden):
        raise ValueError('Runtime data directory must be outside the application source')
    return path


class Store:
    def __init__(self, data_dir=None):
        self.directory = safe_data_dir(default_data_dir() if data_dir is None else data_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'play.sqlite3'
        from .waiting import hub_for
        self.wait_hub = hub_for(self.path)
        self._writer_lock = writer_lock(self.path)
        with self._writer_lock, closing(self.connect()) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS rating_comparisons (
                    comparison_id TEXT PRIMARY KEY, manifest_json TEXT NOT NULL, manifest_sha TEXT NOT NULL,
                    fit_json TEXT NOT NULL, fit_sha TEXT NOT NULL, payload TEXT NOT NULL, checksum TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tables (
                    table_id TEXT PRIMARY KEY, payload TEXT NOT NULL, checksum TEXT NOT NULL,
                    revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS idempotency (
                    scope TEXT NOT NULL, key_hash TEXT NOT NULL, argument_hash TEXT NOT NULL,
                    expires_at REAL NOT NULL, payload TEXT NOT NULL, checksum TEXT NOT NULL,
                    PRIMARY KEY(scope, key_hash));
                CREATE INDEX IF NOT EXISTS idempotency_expiry ON idempotency(expires_at);
                CREATE TABLE IF NOT EXISTS bootstrap (
                    table_id TEXT PRIMARY KEY, payload TEXT NOT NULL, checksum TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS steps (
                    table_id TEXT NOT NULL, step INTEGER NOT NULL, payload TEXT NOT NULL,
                    checksum TEXT NOT NULL, PRIMARY KEY(table_id, step));
                CREATE TABLE IF NOT EXISTS journals (
                    table_id TEXT NOT NULL, viewer INTEGER NOT NULL, sequence INTEGER NOT NULL,
                    payload TEXT NOT NULL, checksum TEXT NOT NULL,
                    PRIMARY KEY(table_id, viewer, sequence));
                CREATE TABLE IF NOT EXISTS table_directory (
                    table_id TEXT PRIMARY KEY, game_id TEXT NOT NULL, status TEXT NOT NULL,
                    created_at REAL NOT NULL, payload TEXT NOT NULL, checksum TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS directory_game_status_created
                    ON table_directory(game_id, status, created_at, table_id);
                CREATE INDEX IF NOT EXISTS directory_created ON table_directory(created_at, table_id);
                CREATE INDEX IF NOT EXISTS directory_status_created ON table_directory(status, created_at, table_id);
            ''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.execute('PRAGMA busy_timeout=30000')
        # WAL: NORMAL keeps the database consistent after a crash; only the last commits may roll back.
        db.execute('PRAGMA synchronous=NORMAL')
        return db

    @contextmanager
    def transaction(self, write=False, *, foreign_keys=False):
        with self._writer_lock if write else nullcontext():
            db = self.connect()
            try:
                if foreign_keys:
                    db.execute('PRAGMA foreign_keys=ON')
                db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
                if write:
                    from .idempotency import current
                    request = current.get()
                    if request is not None and request.store is self:
                        request.lookup(db)
                yield db
                db.commit()
                if write:
                    self.wait_hub.notify()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    @staticmethod
    def checked(row):
        if row is None:
            raise ValueError('Unknown table')
        payload, checksum = row
        if digest(payload) != checksum:
            from .errors import ServiceError
            raise ServiceError('Stored table data is corrupt', code='stored_data_unavailable')
        try:
            return json.loads(payload)
        except (ValueError, TypeError) as error:
            from .errors import ServiceError
            raise ServiceError('Stored table data is corrupt', code='stored_data_unavailable') from error

    def load(self, db, table):
        row = db.execute('SELECT payload, checksum, revision FROM tables WHERE table_id=?', (table,)).fetchone()
        if row is None:
            from .errors import ServiceError
            raise ServiceError('Unknown table; check table_id on this server.', code='unknown_table', argument='table_id')
        state = self.checked(None if row is None else row[:2])
        if state['revision'] != row[2]:
            raise ValueError('Stored table revision is corrupt')
        return state

    @staticmethod
    def save(db, table, state, create=False, *, listed=False, created_at=0):
        if not create:
            state['revision'] += 1
        payload = encode(state)
        if create:
            db.execute('INSERT INTO tables VALUES (?, ?, ?, ?)', (table, payload, digest(payload), state['revision']))
        else:
            db.execute('UPDATE tables SET payload=?, checksum=?, revision=? WHERE table_id=?', (payload, digest(payload), state['revision'], table))
        from . import directory
        if create and listed:
            directory.insert(db, table, state, created_at)
        elif not create:
            directory.update(db, table, state)

    @staticmethod
    def project_journals(table, engine, receipt, cursors):
        from dataclasses import asdict
        rows = []
        for viewer in [None, *range(engine.config.players)]:
            key = -1 if viewer is None else viewer
            for event in engine.project_events(receipt, viewer):
                if event.sequence != cursors[str(key)] + 1:
                    raise ValueError('Stored table journal is corrupt')
                payload = encode(asdict(event))
                rows.append((table, key, event.sequence, payload, digest(payload)))
                cursors[str(key)] = event.sequence
        return rows

    @classmethod
    def append_journals(cls, db, table, engine, receipt, cursors):
        rows = cls.project_journals(table, engine, receipt, cursors)
        db.executemany('INSERT INTO journals VALUES (?, ?, ?, ?, ?)', rows)
