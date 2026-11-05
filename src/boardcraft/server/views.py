"""Explicit sanitized DTOs and capability checks shared by all transports."""
from dataclasses import asdict
import hmac
import re
from .storage import digest
from .decision_clock import command_revision, commands
from .errors import ServiceError


class AccessDenied(ServiceError):
    """The supplied capability cannot perform this operation."""

    def __init__(self, message, *, code='invalid_credential', argument='credential'):
        super().__init__(message, code=code, argument=argument)


class Conflict(ServiceError):
    """The requested decision or lobby transition is no longer available."""

    def __init__(self, message, *, code='conflict', argument=None):
        super().__init__(message, code=code, argument=argument)


SEAT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .'_-]{0,39}$")
SEAT_NAME_RULE = "A seat name uses letters, digits, spaces and . ' _ - (at most 40)"


def seat_name(value):
    """A seat's display name, trimmed; the same rule for the table creator and a player or agent taking the seat."""
    if not isinstance(value, str) or not SEAT_NAME.fullmatch(value.strip()):
        raise ValueError(SEAT_NAME_RULE)
    return value.strip()


def viewer_for(state, credential):
    if credential is None:
        return None
    if not isinstance(credential, str) or len(credential) > 256:
        raise AccessDenied('Invalid seat credential')
    hashed = digest(credential)
    for seat, item in enumerate(state['seats']):
        if item['hash'] is not None and hmac.compare_digest(item['hash'], hashed):
            return seat
    raise AccessDenied('Invalid seat credential')


def creator_for(state, credential):
    if credential is None:
        raise AccessDenied('Creator credential required', code='credential_required')
    if not isinstance(credential, str) or not hmac.compare_digest(state['creator_hash'], digest(credential)):
        raise AccessDenied('Creator credential required')


def seats_view(state):
    return [{'seat': index, 'kind': item.get('control_kind', item['kind']), 'occupied': item['kind'] != 'human' or item['hash'] is not None,
             'display_name': item.get('display_name'), 'bot_ref': item.get('bot_ref'), 'availability': item.get('availability'),
             **({'token': item['token']} if item.get('token') else {})}
            for index, item in enumerate(state['seats'])]


def player_view(table, state, engine, viewer):
    result = {'table_id': table, 'status': state['status'], 'config': state['config'],
              'observation': None if engine is None else asdict(engine.observe(viewer)), 'seats': seats_view(state)}
    result['revision'] = 0 if result['observation'] is None else result['observation']['revision']
    result['event_cursor'] = state['cursors'][str(-1 if viewer is None else viewer)]
    if state.get('live'):
        result['live'] = state['live']
        if viewer is not None:
            deadline = state.get('deadline')
            active = bool(state['seats'][viewer].get('autopilot'))
            permitted = [spec['kind'] for spec in commands(state, engine, viewer)]
            since = state['seats'][viewer].get('autopilot_round') if active else None
            result['control'] = {'autopilot': active, 'can_reclaim': 'reclaim' in permitted,
                                 **({'autopilot_round': since} if type(since) is int else {}),
                                 'commands': permitted,
                                 'deadline': deadline if deadline and deadline['seat'] == viewer else None}
    return result


def actions_view(table, state, engine, viewer, module):
    observation = None if engine is None else engine.observe(viewer)
    decision = None if observation is None else observation.decision
    actions = () if viewer is None or decision is None else engine.legal_actions(viewer)
    def parameter_type(value):
        return {str: 'string', int: 'integer', bool: 'boolean', list: 'array', dict: 'object', float: 'number', type(None): 'null'}[type(value)]
    specs = commands(state, engine, viewer)
    contextual = getattr(module, 'describe_actions', None)
    labels = contextual(actions, observation) if contextual is not None else [module.describe_action(action) for action in actions]
    context = getattr(module, 'decision_context', None)
    facts = context(engine, viewer) if context is not None and engine is not None and viewer is not None else None
    return {'table_id': table, 'commands': specs, **({'expected_revision': command_revision(state, engine, viewer)} if specs else {}),
            **({'context': facts} if facts else {}),
            'decision_id': None if decision is None else decision.id,
            'actions': [dict(asdict(action), label=label,
                             parameter_types={key: parameter_type(value) for key, value in action.params.items()})
                        for action, label in zip(actions, labels)]}
