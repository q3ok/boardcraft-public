"""Safe public diagnostics shared by REST and the MCP dispatch boundary."""

PUBLIC_FIELDS = {'table_id', 'game_id', 'credential', 'decision_id', 'action_id', 'seat', 'config', 'seats',
                 'cursor', 'limit', 'position', 'offset', 'status', 'live', 'visibility', 'kind', 'params',
                 'expected_revision', 'revision', 'spec_sha', 'tournament_id', 'match_id', 'fit', 'sources',
                 'comparison_id', 'result_sha', 'decision_token', 'idempotency_key', 'mode', 'timeout', 'after_change_token'}
PUBLIC_MESSAGES = {
    'conflict': 'The requested operation conflicts with current state or an earlier idempotency request.',
    'busy': 'Server is busy; retry the operation.',
    'bot_unavailable': 'Selected bot version is unavailable',
    'bot_incompatible': 'Selected bot version is incompatible',
    'unknown_table': 'Unknown table; check table_id on this server.',
    'invalid_credential': 'Invalid credential for this operation.',
    'credential_required': 'Credential required; use the seat credential to act or creator credential to start.',
    'stale_decision': 'Decision is stale or unavailable; read legal actions again.',
    'illegal_action': 'Action is not legal for this decision; select a current legal action ID.',
}


def _message(code, argument, constraints, fallback):
    if code == 'out_of_range' and constraints is not None:
        low, high = constraints['minimum'], constraints['maximum']
        return f'{argument.capitalize()} is out of range; allowed range is {low}..{high} for {high - low + 1} positions.'
    if code == 'invalid_arguments':
        return 'Invalid arguments; check the tool schema and current legal actions.' if argument is None else f'Invalid argument {argument}; check its type and constraints.'
    return PUBLIC_MESSAGES.get(code, fallback)


class ServiceError(ValueError):
    """An explicitly public message with stable, input-independent metadata."""

    def __init__(self, message, *, code='invalid_arguments', argument=None, constraints=None):
        super().__init__(message)
        self.code, self.argument = code, argument
        self.constraints = constraints


def error_view(error):
    from boardcraft.inference.catalog import BotIncompatible, BotUnavailable
    if isinstance(error, BotUnavailable):
        incompatible = isinstance(error, BotIncompatible)
        error = ServiceError('Selected bot version is incompatible' if incompatible else 'Selected bot version is unavailable',
                             code='bot_incompatible' if incompatible else 'bot_unavailable')
    if not isinstance(error, ServiceError):
        error = ServiceError('Invalid arguments; check the tool schema and current legal actions.')
    data = {'code': error.code, 'message': _message(error.code, error.argument, error.constraints, str(error))}
    if error.argument is not None:
        data['argument'] = error.argument
    if error.constraints is not None:
        data['constraints'] = dict(error.constraints)
    return data


def remote_error(data):
    """Accept only bounded diagnostic metadata; never trust remote message text."""
    if type(data) is not dict or data.get('isError') is not True:
        return None
    item = data.get('structuredContent')
    if type(item) is not dict or item.get('code') not in {*PUBLIC_MESSAGES, 'out_of_range', 'invalid_arguments'}:
        return None
    code, argument, constraints = item['code'], item.get('argument'), item.get('constraints')
    if argument is not None and (type(argument) is not str or argument not in PUBLIC_FIELDS):
        return None
    if constraints is not None:
        if (type(constraints) is not dict or set(constraints) - {'minimum', 'maximum', 'min_length', 'max_length'}
                or any(type(value) is not int or not 0 <= value <= 2**63 - 1 for value in constraints.values())):
            return None
    if code == 'out_of_range' and (argument not in ('position', 'cursor', 'seat') or constraints is None
                                  or set(constraints) != {'minimum', 'maximum'}
                                  or constraints['minimum'] > constraints['maximum']):
        return None
    return ServiceError(_message(code, argument, constraints, ''), code=code, argument=argument, constraints=constraints)


def invalid_arguments(error, model=None):
    """Project validation failures without input values or validator exceptions."""
    first = error.errors()[0]
    location = [part for part in first['loc'] if part not in ('body', 'query', 'path', 'header')]
    properties = {} if model is None else model.model_json_schema().get('properties', {})
    argument = location[0] if location and location[0] in (properties if model is not None else PUBLIC_FIELDS) else None
    schema = properties.get(argument, {})
    names = {'minimum': 'minimum', 'maximum': 'maximum', 'minLength': 'min_length', 'maxLength': 'max_length'}
    constraints = {target: schema[source] for source, target in names.items() if source in schema}
    # REST validation provides bounds in ctx; never reflect its arbitrary input/msg.
    context = first.get('ctx', {})
    for source, target in (('ge', 'minimum'), ('le', 'maximum'), ('min_length', 'min_length'), ('max_length', 'max_length')):
        if type(context.get(source)) in (int, float):
            constraints[target] = context[source]
    message = 'Invalid arguments; check the tool schema.' if argument is None else f'Invalid argument {argument}; check its type and constraints.'
    return ServiceError(message, argument=argument, constraints=constraints or None)
