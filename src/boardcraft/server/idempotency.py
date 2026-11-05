"""Atomic 24-hour acknowledgements; retry keys are secret recovery capabilities.

Credential acknowledgements store only random derivation nonces. Recovering a
credential requires the original high-entropy client key, which is never stored.
"""
from contextvars import ContextVar
from dataclasses import is_dataclass, asdict
from functools import wraps
import hmac
import inspect
import secrets
from .storage import digest, encode, Store
from .views import Conflict

RETENTION_SECONDS = 86400
current = ContextVar('boardcraft_idempotency', default=None)


class Replay(Exception):
    def __init__(self, response):
        self.response = response


class Request:
    def __init__(self, store, scope, key, arguments, now):
        self.store, self.scope, self.key, self.now = store, scope, key, now
        self.key_hash, self.argument_hash = digest(key), digest(encode(arguments))
        self.nonces = {}
        self.response = None

    def credential(self, nonce):
        return hmac.new(self.key.encode(), ('boardcraft-credential-v1:' + self.scope + ':' + nonce).encode(), 'sha256').hexdigest()

    def lookup(self, db):
        row = db.execute('SELECT argument_hash, expires_at, payload, checksum FROM idempotency WHERE scope=? AND key_hash=?',
                         (self.scope, self.key_hash)).fetchone()
        if row is None or row[1] <= self.now():
            return
        if not hmac.compare_digest(row[0], self.argument_hash):
            raise Conflict('Idempotency key was already used with different arguments.', argument='idempotency_key')
        packed = Store.checked(row[2:])
        response = packed['response']
        for field, nonce in packed['credentials'].items():
            response[field] = self.credential(nonce)
        raise Replay(response)

    def remember(self, db, response):
        public = dict(response)
        credentials = {}
        for field in ('credential', 'creator_credential'):
            if field in public:
                credentials[field] = self.nonces[public.pop(field)]
        payload = encode({'response': public, 'credentials': credentials})
        now = self.now()
        db.execute('DELETE FROM idempotency WHERE expires_at<=?', (now,))
        db.execute('INSERT INTO idempotency VALUES (?, ?, ?, ?, ?, ?)',
                   (self.scope, self.key_hash, self.argument_hash, now + RETENTION_SECONDS, payload, digest(payload)))
        self.response = response


def issue_credential():
    context = current.get()
    if context is None:
        return secrets.token_urlsafe(32)
    nonce = secrets.token_hex(32)
    credential = context.credential(nonce)
    context.nonces[credential] = nonce
    return credential


def remember(db, store, response):
    context = current.get()
    if context is not None and context.store is store:
        context.remember(db, response)


def idempotent(function):
    signature = inspect.signature(function)
    @wraps(function)
    def run(self, *args, idempotency_key=None, **kwargs):
        if idempotency_key is None:
            return function(self, *args, **kwargs)
        if (not isinstance(idempotency_key, str) or not 32 <= len(idempotency_key) <= 256
                or any(not 33 <= ord(char) <= 126 for char in idempotency_key)):
            raise ValueError('Idempotency keys must be 32..256 printable ASCII characters with at least 128 bits of client randomness')
        bound = signature.bind(self, *args, **kwargs)
        bound.apply_defaults()
        arguments = {name: asdict(value) if is_dataclass(value) else value
                     for name, value in bound.arguments.items() if name not in ('self', 'credential')}
        authority = digest(bound.arguments['credential']) if isinstance(bound.arguments.get('credential'), str) else 'anonymous'
        scope = encode([function.__name__, bound.arguments.get('table_id'), authority])
        request = Request(self.store, scope, idempotency_key, arguments, self.clock)
        previous = current.set(request)
        try:
            try:
                with self.store.transaction() as db:
                    request.lookup(db)
                try:
                    result = function(self, *args, **kwargs)
                    return request.response if request.response is not None else result
                except ValueError:
                    # A concurrent identical call may have committed between
                    # the first replay check and validation of the old decision.
                    with self.store.transaction() as db:
                        request.lookup(db)
                    raise
            except Replay as replay:
                return replay.response
        finally:
            current.reset(previous)
    return run
