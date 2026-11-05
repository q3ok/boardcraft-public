"""Bounded local bot execution; choices run outside store transactions."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from copy import deepcopy
import hashlib
import threading
from boardcraft.contracts import Action, BotChoice, BotReference, Observation
from boardcraft.inference.catalog import BotUnavailable, reference_from_data, reference_data


@dataclass(frozen=True)
class BotWork:
    table_id: str
    seat: int
    kind: str
    seed: int
    observation: Observation
    actions: tuple[Action, ...]
    reference: BotReference
    expected_revision: int | None = None
    state_before: dict | None = None


@dataclass(frozen=True)
class StatefulChoice(BotChoice):
    state_after: dict = field(repr=False)
    work: BotWork = field(repr=False, compare=False)


def prepare_work(table, state, engine, *, stateful):
    if engine is None or state['status'] != 'playing':
        return []
    work = []
    for seat, item in enumerate(state['seats']):
        if item['kind'] == 'human':
            continue
        availability = item.get('availability')
        if availability and availability.get('reference') == item.get('bot_ref') and availability.get('status') == 'unavailable':
            continue
        observation = engine.observe(seat)
        if observation.decision is None:
            continue
        coordinate = (f"{state['seed']}:{seat}" if stateful else
                      f"{state['seed']}:{seat}:{observation.decision.id}").encode()
        seed = int.from_bytes(hashlib.sha256(coordinate).digest()[:8], 'big')
        work.append(BotWork(table, seat, item['kind'], seed, observation, engine.legal_actions(seat), reference_from_data(item['bot_ref']),
                            state['revision'] if stateful else None, deepcopy(item.get('bot_state'))))
    return work


def unchanged(work, state, engine):
    return (engine is not None and state['status'] == 'playing'
            and (work.expected_revision is None or state['revision'] == work.expected_revision)
            and state['seats'][work.seat]['kind'] == work.kind
            and state['seats'][work.seat].get('bot_ref') == reference_data(work.reference)
            and engine.observe(work.seat) == work.observation
            and engine.legal_actions(work.seat) == work.actions)


class BotExecutor:
    def __init__(self, service, workers=2):
        self.service = service
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='boardcraft-bot')
        self.capacity = threading.BoundedSemaphore(workers)
        self.pending = set()
        self.last_table = ''
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._poll, name='boardcraft-tables', daemon=True)

    def start(self):
        self.thread.start()

    def _poll(self):
        while not self.stop.is_set():
            with self.service.store.transaction() as db:
                tables = [row[0] for row in db.execute('SELECT table_id FROM tables ORDER BY table_id')]
            tables = [table for table in tables if table > self.last_table] + [table for table in tables if table <= self.last_table]
            for table in tables:
                if self.stop.is_set():
                    break
                with self.lock:
                    if table in self.pending or not self.capacity.acquire(False):
                        continue
                    self.pending.add(table)
                    self.last_table = table
                self.pool.submit(self._run, table)
            self.stop.wait(.05)

    def _run(self, table):
        try:
            self.service.refresh_bot_resources(table)
            # A table armed to start by itself starts here, on the server, once every seat is ready.
            try:
                self.service.autostart_if_ready(table)
            except Exception:  # noqa: BLE001 -- the next look tries again
                pass
            if self.service.tick(table):
                return
            for work in self.service.bot_work(table):
                if self.stop.is_set():
                    break
                try:
                    choice = self.service.choose_bot(work)
                    self.service.apply_bot(work, choice)
                except BotUnavailable:
                    # Resource status is checked independently of decision progress.
                    continue
        except ValueError:
            pass
        finally:
            with self.lock:
                self.pending.discard(table)
                self.capacity.release()

    def close(self):
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join()
        self.pool.shutdown(wait=True, cancel_futures=True)
