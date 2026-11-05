"""Durable live-play scheduling and the Rentcraft table's passive policy."""
from math import nextafter

from boardcraft.contracts import Action
from boardcraft.settings import normalize_settings


def live_schema(game_id, people=True, watchers=None):
    watchers = people if watchers is None else watchers
    properties = {}
    if game_id == 'rentcraft':
        # `order` and `presets` only shape the "New table" form (one row with the game's own options).
        properties = {
            'agent_timeout': {'type': ['number', 'null'], 'minimum': nextafter(0., 1.), 'default': 60.,
                              'title': 'Decision time limit', 'order': 2,
                              'presets': [{'value': 30., 'label': '30 s'}, {'value': 60., 'label': '60 s'},
                                          {'value': 120., 'label': '120 s'}, {'value': None, 'label': 'no limit'}],
                              'description': 'Agents always; people only with autopilot on. Seconds for each decision; no limit when empty.'},
            'autopilot': {'type': 'boolean', 'default': False, 'title': 'Autopilot after a timeout', 'order': 8,
                          'description': 'When a human or an agent runs out of decision time, a passive autopilot plays for them '
                                         'instead of a surrender; they come back with one click. The time limit then applies to people too.'},
            'trade_window_wait_s': {'type': 'number', 'minimum': 0, 'maximum': 60,
                                    'default': 3. if people else 0., 'automatic': True,
                                    'title': 'Reaction time (s)', 'order': 4,
                                    'description': 'How many seconds the game waits in each offer window for a human or an agent; '
                                                   '0 = no waiting. The default is 3 with people or agents and 0 with bots only.'},
            # bots move at a watchable pace when a person sits at the table, at once with only bots and agents
            'bot_delay': {'type': 'number', 'minimum': 0, 'default': .6 if watchers else 0., 'automatic': True,
                          'title': 'Bot speed', 'order': 9,
                          'presets': [{'value': 1.2, 'label': 'slow'}, {'value': .6, 'label': 'normal'},
                                      {'value': .15, 'label': 'fast'}, {'value': 0., 'label': 'instant'}],
                          'description': 'Seconds per bot move.'},
        }
    return {'type': 'object', 'properties': properties, 'additionalProperties': False}


def live_settings(game_id, seats, values):
    schema = live_schema(game_id, any(item['kind'] == 'human' for item in seats),
                         any(item['kind'] == 'human' and item.get('control_kind') != 'agent' for item in seats))
    # `automatic` only tells a UI that the default depends on the table roster; it is not a validated setting keyword.
    checked = {**schema, 'properties': {key: {name: item for name, item in field.items() if name != 'automatic'}
                                         for key, field in schema['properties'].items()}}
    overrides = normalize_settings(checked, values)
    return {**{k: v['default'] for k, v in schema['properties'].items()}, **overrides}


def deadline_effect(engine, decision, kind, autopilot):
    if kind == 'window':
        return 'pass'
    if autopilot:
        return 'autopilot'
    if decision.kind in ('trade_window', 'mortgage_takeover'):
        return 'pass'
    if [a.kind for a in engine.legal_actions(decision.player)] == ['declare_bankruptcy']:
        return 'declare_bankruptcy'
    return 'resign'


def arm(state, engine, now, *, preserve=False, pending_request_decision=None):
    if not state.get('live'):
        return
    for item, player in zip(state['seats'], engine.game.state.players):
        if player.bankrupt or engine.is_over():
            item.pop('autopilot', None)
            item.pop('autopilot_round', None)
    previous = state.get('deadline')
    state['deadline'] = None
    prior_automatic_at = state.get('automatic_at')
    state['automatic_at'] = None
    decision = engine.decision()
    if decision is None:
        return
    seat = decision.player
    item = state['seats'][seat]
    live = state['live']
    if item['kind'] != 'human' or item.get('autopilot'):
        same_decision = (pending_request_decision is not None and pending_request_decision.player == seat
                         and pending_request_decision.kind == decision.kind)
        state['automatic_at'] = prior_automatic_at if same_decision and prior_automatic_at is not None else now + live['bot_delay']
        return
    window = engine.game.state.window
    if decision.kind == 'trade_window' and seat not in window.requested:
        kind, seconds = 'window', live['trade_window_wait_s']
    elif item.get('control_kind') == 'agent' or live['autopilot']:
        kind, seconds = 'decision', live['agent_timeout']
    else:
        return
    if seconds is None:
        return
    same = (preserve and previous and previous['seat'] == seat and previous['phase'] == decision.kind
            and previous['kind'] == kind)
    state['deadline'] = {'seat': seat, 'decision_id': decision.id, 'kind': kind, 'phase': decision.kind,
                         'at': previous['at'] if same else now + seconds,
                         'effect': deadline_effect(engine, decision, kind, live['autopilot'])}


def liquidation_action(engine, seat):
    decision = engine.decision()
    if decision and decision.kind == 'raise_funds' and decision.player == seat:
        from boardcraft.games.rentcraft.engine import public_action
        from boardcraft.games.rentcraft.liquidity import liquidation_step
        action = liquidation_step(engine.game.state, seat)
        if action is not None:
            return public_action(action)
    return None


def liquidation_actions(engine, seat):
    """Advance the same local engine between yields. Never carry authority into the next debt."""
    debt = engine.game.state.debts[0]
    while engine.game.state.debts and engine.game.state.debts[0] is debt:
        action = liquidation_action(engine, seat)
        if action is None:
            break
        yield action


def passive_action(engine, decision):
    actions = engine.legal_actions(decision.player)
    if decision.kind == 'raise_funds':
        return liquidation_action(engine, decision.player) or Action('declare_bankruptcy', 'declare_bankruptcy', {})
    preferred = ('roll', 'end_turn', 'decline', 'pass_bid', 'pass', 'reject_trade')
    return next((a for kind in preferred for a in actions if a.kind == kind), actions[0])


def control_spec(kind, label, scope):
    return {'kind': kind, 'label': label, 'scope': scope,
            'params_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}}


def live_seat_operations_only(state):
    from boardcraft.registry import get_game
    try:
        module = get_game(state['config']['game_id'])
    except KeyError:  # a module supplied to the service directly declares no live-only operations
        return False
    caps = module.info().capabilities
    return getattr(module, 'live_seat_operations', False) and not caps.seat_operations


def command_revision(state, engine, seat):
    """The revision a seat command is sent with. A live-only seat operation uses the seat's own perspective revision:
    the table revision also moves on other seats' hidden simultaneous choices and would reveal them."""
    if live_seat_operations_only(state):
        return engine.observe(seat).revision
    return state['revision']


def commands(state, engine, seat):
    if engine is None or seat is None:
        return []
    specs = list(engine.command_specs(seat))
    if specs and live_seat_operations_only(state):
        # Live-only seat operations (Dice surrender) are for people at the table: an older table's history cannot
        # record them, and an agent waiting for its turn must not be woken by a command it can always send.
        seat_item = state['seats'][seat]
        if state.get('record_version') != '3' or seat_item['kind'] != 'human' or seat_item.get('control_kind') == 'agent':
            return []
    if specs and state.get('live'):
        if state['seats'][seat].get('autopilot'):
            specs.append(control_spec('reclaim', 'Take back control', 'seat'))
        if liquidation_action(engine, seat):
            specs.append(control_spec('liquidate', 'Raise cash at the bank for this debt', 'decision'))
    return specs


def seat_active(state, observation, seat):
    """Generic observation flag, with the existing Rentcraft policy adapter.

    Games with elimination expose seat_active in the permitted observation;
    older Rentcraft observations already expose the public bankruptcy flag.
    """
    if seat is None or observation is None:
        return None
    if state['config']['game_id'] == 'rentcraft':
        return not observation['data']['players'][seat]['bankrupt']
    return observation['data'].get('seat_active', True)
