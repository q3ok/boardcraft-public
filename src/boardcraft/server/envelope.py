"""Versioned, perspective-local service snapshots (independent of transports)."""
import hmac
from .storage import encode, digest
from .views import actions_view, player_view, Conflict
from .decision_clock import seat_active


def token(state, purpose, value):
    return hmac.new(state['creator_hash'].encode(), (purpose + ':' + encode(value)).encode(), 'sha256').hexdigest()


def permitted_content(value):
    """Engine CAS identities/counters are not observable view-change signals."""
    result = {key: item for key, item in value.items()
              if key not in ('server_time', 'revision', 'decision_token', 'change_token')}
    def decision(item):
        return None if item is None else {key: part for key, part in item.items() if key != 'id'}
    result['decision'] = decision(result['decision'])
    if result['observation'] is not None:
        result['observation'] = {key: item for key, item in result['observation'].items() if key != 'revision'}
        result['observation']['decision'] = decision(result['observation']['decision'])
    return result


def advance_perspectives(table, state, engine, module, *, actor=None):
    """Persist view generations so returning to an old view cannot revive a token.

    Own successful operations always consume the generation, including a game
    immediately offering an identical next decision. Hidden other-seat changes
    cannot change another perspective's generation.
    """
    versions = state.setdefault('perspective_epochs', {})
    for viewer in [None, *range(state['config']['players'])]:
        key = str(viewer)
        value = envelope(table, state, engine, viewer, module, 0.)
        fingerprint = digest(encode(permitted_content(value)))
        previous = versions.get(key)
        generation = 0 if previous is None else previous['generation']
        if previous is not None and (previous['fingerprint'] != fingerprint or actor is not None and viewer == actor):
            generation += 1
        versions[key] = {'generation': generation, 'fingerprint': fingerprint}


def envelope(table, state, engine, viewer, module, now):
    value = player_view(table, state, engine, viewer)
    choices = actions_view(table, state, engine, viewer, module)
    observation = value['observation']
    decision = None if observation is None or viewer is None else observation['decision']
    deadline = state.get('deadline')
    if deadline and (viewer is None or deadline['seat'] != viewer):
        deadline = None
    value.update(contract_version='1', perspective={'role': 'observer' if viewer is None else 'seat', 'seat': viewer},
                 decision=decision, actions=choices['actions'], commands=choices['commands'],
                 deadline=deadline, inaction_effect=None if deadline is None else deadline['effect'],
                 server_time=now, seat_active=seat_active(state, observation, viewer))
    generation = state.get('perspective_epochs', {}).get(str(viewer), {}).get('generation', 0)
    permitted = permitted_content(value)
    value['decision_token'] = (token(state, 'decision', [table, viewer, generation, permitted])
                               if viewer is not None and (decision is not None or value['commands']) else None)
    # Internal/global revision counters are never a readiness signal. Modules
    # supply perspective observations; tokens only depend on permitted content.
    value['change_token'] = token(state, 'change', [generation, permitted])
    return value


def validate_token(supplied, current):
    if supplied is not None and (not isinstance(supplied, str) or current is None or not hmac.compare_digest(supplied, current)):
        raise Conflict('Decision is stale or unavailable; read the current envelope.', code='stale_decision', argument='decision_token')
