"""Reconstruct trusted recordings from bootstrap and accepted event deltas."""
from boardcraft.registry import get_game
from dataclasses import asdict
import json
from boardcraft.contracts import Event, GameEngine
from .records import RecordedDecision, RecordedOperation, validate_record
from .runner import project_receipt, record_version


def _same_events(left, right):
    return json.dumps([asdict(e) for e in left], sort_keys=True) == json.dumps([asdict(e) for e in right], sort_keys=True)


def apply_recorded(engine: GameEngine, step: RecordedDecision | RecordedOperation) -> tuple[Event, ...]:
    decision = step.decision
    operation = isinstance(step, RecordedOperation)
    actor = step.actor if operation else decision.player
    observation = engine.observe(actor)
    if operation and observation.revision != step.before_revision:
        raise ValueError('Game record operation revision mismatch')
    if decision is None:
        events = engine.apply_seat_operation(actor, step.action, step.before_revision)
    else:
        if decision.player != actor or observation.decision != decision:
            raise ValueError('Game record decision mismatch')
        events = engine.apply(actor, step.action, decision.id)
    if not _same_events(events, step.events):
        raise ValueError('Game record events mismatch')
    return events


def replay(record, through_decision: int | None = None, *, module=None):
    record = validate_record(record, module=module)
    if through_decision is None:
        if not record.complete:
            raise ValueError('Cannot fully replay an incomplete game record')
        count = len(record.steps)
    else:
        if type(through_decision) is not int or not 0 <= through_decision <= len(record.steps):
            raise ValueError('Replay decision prefix is out of range')
        count = through_decision
    module = get_game(record.config.game_id) if module is None else module
    if (module.game_id, module.rules_version, module.engine_version) != (record.config.game_id, record.rules_version, record.engine_version):
        raise ValueError('Incompatible game record engine or rules version')
    live_operations = getattr(module, 'live_seat_operations', False)
    if (record_version(module) == '3') != (record.version == '3') and not (live_operations and record.version == '3'):
        raise ValueError('Incompatible game record version for this module')
    try:
        engine = module.restore(record.initial_snapshot)
        if engine.config != record.config:
            raise ValueError('Game record configuration does not match the restored game')
        if not _same_events(engine.initial_events(), record.initial_events):
            raise ValueError('Game record bootstrap events do not match the snapshot')
        project_receipt(engine, record.initial_events, record.config.players)
        if record.version == '3' and engine.observe(None).revision != getattr(module, 'initial_revision', 0):
            raise ValueError('Game record bootstrap is not the initial revision')
        for step in record.steps[:count]:
            events = apply_recorded(engine, step)
            project_receipt(engine, events, record.config.players)
        if count == len(record.steps) and record.complete:
            if not engine.is_over() or engine.result() != record.final_result:
                raise ValueError('Game record final result does not match replay')
        elif engine.is_over():
            raise ValueError('Game record is incomplete or has decisions after completion')
        return engine
    except (KeyError, TypeError, AttributeError, IndexError) as error:
        raise ValueError('Invalid game record replay data') from error
