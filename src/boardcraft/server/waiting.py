"""Perspective waits: register before checking, no transaction across awaits."""
import asyncio
from contextlib import contextmanager
import math
import threading
from weakref import WeakValueDictionary
from .errors import ServiceError

WAIT_LIMIT = 32
MAX_WAIT_SECONDS = 25.
_hubs = WeakValueDictionary()
_guard = threading.Lock()


class WaitHub:
    def __init__(self):
        self._lock = threading.Lock()
        self._listeners = set()

    @property
    def count(self):
        with self._lock:
            return len(self._listeners)

    @contextmanager
    def subscribe(self):
        loop, event = asyncio.get_running_loop(), asyncio.Event()
        listener = (loop, event)
        with self._lock:
            if len(self._listeners) >= WAIT_LIMIT:
                raise ServiceError('Too many waiting calls; retry later.', code='busy')
            self._listeners.add(listener)
        try:
            yield event
        finally:
            with self._lock:
                self._listeners.discard(listener)

    def notify(self):
        with self._lock:
            for loop, event in self._listeners:
                if not loop.is_closed():
                    loop.call_soon_threadsafe(event.set)


def hub_for(path):
    with _guard:
        hub = _hubs.get(path)
        if hub is None:
            hub = WaitHub()
            _hubs[path] = hub
        return hub


async def wait_for_decision(service, table_id, credential=None, *, mode='actionable', after_change_token=None, timeout=25.):
    if (mode not in ('actionable', 'changed') or type(timeout) not in (int, float)
            or not math.isfinite(timeout) or not 0 <= timeout <= MAX_WAIT_SECONDS
            or after_change_token is not None and (not isinstance(after_change_token, str) or len(after_change_token) != 64)):
        raise ValueError('Invalid wait mode, change token or timeout (0..25 seconds)')
    if mode == 'actionable' and credential is None:
        raise ValueError('Actionable waits require a seat credential; observers use changed')
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    baseline = after_change_token
    with service._wait_hub.subscribe() as event:
        while True:
            event.clear()
            # The executor only holds a worker for one short snapshot, never for
            # sleeping. It is separate from the MCP short-call AnyIO limiter.
            view = await asyncio.to_thread(service.get_view, table_id, credential)
            observation = view['observation']
            reason = None
            if view['status'] == 'completed':
                reason = 'completed'
            elif credential is not None and view['seat_active'] is False:
                reason = 'seat_inactive'
            elif mode == 'actionable' and (view['decision'] is not None or view['commands']):
                reason = 'actionable'
            elif mode == 'changed' and baseline is not None and baseline != view['change_token']:
                reason = 'changed'
            elif loop.time() >= end:
                reason = 'timeout'
            if reason is not None:
                return {**view, 'reason': reason}
            baseline = view['change_token'] if baseline is None else baseline
            try:
                # Periodic checks cover commits from another server process too.
                await asyncio.wait_for(event.wait(), min(.25, max(0., end - loop.time())))
            except asyncio.TimeoutError:
                pass
