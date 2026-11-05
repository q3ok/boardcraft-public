"""Completed-game reconstruction exposing only a selected permitted view.

A completed table never changes, so its permitted replay is indexed once per viewer: one validated pass over the
record keeps the events and summary metrics of every position and an engine checkpoint every few positions. A frame
is then rebuilt from the nearest checkpoint with at most CHECKPOINT_EVERY - 1 already validated steps.
"""
import copy
import threading
from collections import OrderedDict
from dataclasses import asdict
from boardcraft.play.records import RecordedOperation, _decode, participant_data, participants_from_seats
from boardcraft.play.replay import apply_recorded, replay
from .errors import ServiceError

CHECKPOINT_EVERY = 16
CACHE_SIZE = 4
_lock = threading.Lock()


def _out_of_range(last):
    return ServiceError(f'Replay position is unavailable; allowed range is 0..{last} for {last + 1} positions.',
                        code='out_of_range', argument='position', constraints={'minimum': 0, 'maximum': last})


def decision_note(module, step):
    """Who decided, what, and the player's recorded reasoning (action probabilities, a value estimate, an explanation)."""
    from .service import detached
    actor = step.actor if isinstance(step, RecordedOperation) else step.decision.player
    try:
        label = module.describe_action(step.action)
    except Exception:  # noqa: BLE001 -- an action without a description is shown by its id
        label = step.action.id
    note = {'player': actor, 'action': label, 'explanation': step.explanation or ''}
    if step.meta:
        note['meta'] = detached(dict(step.meta))
    return note


def _apply_validated(engine, step):
    """Re-apply a step that the index already checked against the record."""
    if isinstance(step, RecordedOperation):
        if step.decision is None:
            return engine.apply_seat_operation(step.actor, step.action, step.before_revision)
        return engine.apply(step.actor, step.action, step.decision.id)
    return engine.apply(step.decision.player, step.action, step.decision.id)


class ReplayIndex:
    """Positions of one viewer's permitted replay: events, metrics and engine checkpoints."""

    def __init__(self, record, module, viewer):
        from .service import detached
        self.viewer, self.steps = viewer, record.steps
        metrics = getattr(module, 'replay_metrics', None)
        summary = (lambda observation: metrics(observation)) if metrics else (lambda observation: None)
        engine = replay(record, 0, module=module)
        observation = engine.observe(viewer)
        self.positions = [{'step': -1, 'metrics': summary(observation),
                           'events': detached([asdict(e) for e in engine.project_events(engine.initial_events(), viewer)])}]
        self.checkpoints = {0: copy.deepcopy(engine)}
        for index, step in enumerate(record.steps):
            previous = observation.revision
            permitted = engine.project_events(apply_recorded(engine, step), viewer)
            observation = engine.observe(viewer)
            # Silent operations can still advance this viewer's public decision.
            if permitted or (record.version == '3' and observation.revision != previous):
                self.positions.append({'step': index, 'metrics': summary(observation),
                                       'events': detached([asdict(e) for e in permitted]),
                                       'decision': decision_note(module, step) if permitted else None})
                if (len(self.positions) - 1) % CHECKPOINT_EVERY == 0:
                    self.checkpoints[len(self.positions) - 1] = copy.deepcopy(engine)
        if not engine.is_over() or engine.result() != record.final_result:
            raise ValueError('Stored game record result is corrupt')

    @property
    def total(self):
        return len(self.positions)

    def frame(self, position):
        from .service import detached
        if type(position) is not int or position < 0:
            raise ValueError('Replay position is unavailable for this viewer')
        if position >= self.total:
            raise _out_of_range(self.total - 1)
        start = position - position % CHECKPOINT_EVERY
        engine = copy.deepcopy(self.checkpoints[start])
        for step in self.steps[self.positions[start]['step'] + 1:self.positions[position]['step'] + 1]:
            _apply_validated(engine, step)
        return {'observation': detached(asdict(engine.observe(self.viewer))),
                'events': copy.deepcopy(self.positions[position]['events']),
                'position': position, 'total_positions': self.total}

    def timeline(self):
        """Every position at a glance: who acted, the kinds of its events and the game's summary metrics."""
        return {'total_positions': self.total, 'positions': [
            {'position': n, 'events': [{'kind': e['kind'], 'player': e['payload'].get('player')} for e in item['events']],
             'metrics': copy.deepcopy(item['metrics']), 'decision': copy.deepcopy(item.get('decision'))}
            for n, item in enumerate(self.positions)]}


def replay_index(service, state, viewer, bootstrap_row, rows, key):
    """The cached index of a completed table for one viewer (built once, least recently used entries dropped)."""
    with _lock:
        _cache = service.__dict__.setdefault('_replay_indexes', OrderedDict())
        found = _cache.get(key)
        if found is not None:
            _cache.move_to_end(key)
            return found
        record, module = _table_record(service, state, bootstrap_row, rows)
        index = ReplayIndex(record, module, viewer)
        _cache[key] = index
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
        return index


def replay_view(service, state, viewer, position, bootstrap_row, rows, key=None):
    if type(position) is not int or position < 0:
        raise ValueError('Replay position is unavailable for this viewer')
    if key is not None:
        return replay_index(service, state, viewer, bootstrap_row, rows, key).frame(position)
    record, module = _table_record(service, state, bootstrap_row, rows)
    return project_record(record, module, viewer, position)


def _table_record(service, state, bootstrap_row, rows):
    from .service import detached
    bootstrap = service.store.checked(bootstrap_row)
    if [row[0] for row in rows] != list(range(1, state['steps'] + 1)):
        raise ValueError('Stored game record is corrupt')
    steps = [service.store.checked(row[1:]) for row in rows]
    module = service.module(state['config']['game_id'])
    final = service._engine(state).result()
    participants = {}
    if 'participants' in bootstrap:
        frozen = [participant_data(item) for item in participants_from_seats(state['seats'])]
        if bootstrap['participants'] != frozen:
            raise ValueError('Stored game participants are corrupt')
        participants = {'participants': bootstrap['participants']}
    version = bootstrap.get('record_version', '2' if participants else '1')
    if (bootstrap.get('record_version') != state.get('record_version')
            or 'record_version' in bootstrap and version != '3'):
        raise ValueError('Stored game record version is corrupt')
    rules_version, engine_version = module.rules_version, module.engine_version
    config = dict(state['config'])
    if version == '3':
        if (bootstrap.get('rules_version'), bootstrap.get('engine_version')) != (rules_version, engine_version):
            raise ValueError('Stored game record has missing or incompatible versions')
        rules_version, engine_version = bootstrap['rules_version'], bootstrap['engine_version']
        config.setdefault('settings', {})
    record = _decode({'version': version, 'config': config, 'rules_version': rules_version,
                      'engine_version': engine_version, 'initial_snapshot': bootstrap['snapshot'],
                      'initial_events': bootstrap['events'], ('operations' if version == '3' else 'decisions'): steps, 'complete': True,
                      'final_result': detached(asdict(final)), **participants}, module=module)
    return record, module


def project_record(record, module, viewer, position):
    """The same permitted replay frames for tables and complete batch records."""
    from .service import detached
    if type(position) is not int or position < 0:
        raise ValueError('Replay position is unavailable for this viewer')
    engine = replay(record, 0, module=module)
    def frame(events):
        return detached({'observation': asdict(engine.observe(viewer)),
                         'events': [asdict(event) for event in events]})
    selected = frame(engine.project_events(engine.initial_events(), viewer)) if position == 0 else None
    local_position = 0
    for step in record.steps:
        previous_revision = engine.observe(viewer).revision if record.version == '3' else None
        receipt = apply_recorded(engine, step)
        permitted = engine.project_events(receipt, viewer)
        # Silent operations can still advance this viewer's public decision.
        if permitted or (record.version == '3' and engine.observe(viewer).revision != previous_revision):
            local_position += 1
            if local_position == position:
                selected = frame(permitted)
    if not engine.is_over() or engine.result() != record.final_result:
        raise ValueError('Stored game record result is corrupt')
    if selected is None:
        raise ServiceError(f'Replay position is unavailable; allowed range is 0..{local_position} for {local_position + 1} positions.',
                           code='out_of_range', argument='position', constraints={'minimum': 0, 'maximum': local_position})
    return dict(selected, position=position, total_positions=local_position + 1)
