"""Controlled JSON persistence for bounded turn state and delivery receipts."""
from boardcraft.contracts import Event, GameConfig, JsonValue, _copy_json
from .actions import reward_catalog
from .bonuses import BonusStack
from .randomness import DICE, KeyedRandom
from .sheet import load_sheet
from .state import PlayerSheet
from .turn_actions import window_actions
from .turn_state import PlayerWindow, TurnState, sheet_view


def integer(value, low=0, high=None):
    if type(value) is not int or value < low or (high is not None and value > high):
        raise ValueError('Invalid integer in game snapshot')
    return value


def keys(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError('Invalid fields in game snapshot')


def sequence(value):
    if not isinstance(value, list):
        raise ValueError('Invalid list in game snapshot')
    return value


def unique(value):
    items = sequence(value)
    if len(items) != len(set(items)):
        raise ValueError('Duplicate value in game snapshot')
    return items


def restore_sheet(data):
    keys(data, ('yellow','blue','green','orange','purple','earned_rewards','rerolls','extras','emblem_count',
                'rerolls_earned','rerolls_used','extras_earned','extras_used'))
    spec = load_sheet()
    yellow = []
    for pair in sequence(data['yellow']):
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError('Invalid yellow field')
        row, column = (integer(v, 0, 3) for v in pair)
        if spec.yellow.rows[row][column] is None and (row, column) not in spec.yellow.premarked:
            raise ValueError('Invalid yellow field')
        yellow.append((row, column))
    if len(yellow) != len(set(yellow)) or not spec.yellow.premarked <= set(yellow):
        raise ValueError('Invalid yellow marks')
    blue = {integer(v, 2, 12) for v in unique(data['blue'])}
    green = integer(data['green'], 0, len(spec.green.thresholds))
    orange = sequence(data['orange'])
    purple = sequence(data['purple'])
    if len(orange) > len(spec.orange.multipliers) or len(purple) > spec.purple.length:
        raise ValueError('Invalid track length')
    for index, value in enumerate(orange):
        integer(value, 1, 6 * spec.orange.multipliers[index])
        if value % spec.orange.multipliers[index]:
            raise ValueError('Invalid recorded orange value')
    for index, value in enumerate(purple):
        integer(value, 1, 6)
        if index and purple[index-1] != 6 and value <= purple[index-1]:
            raise ValueError('Invalid purple sequence')
    earned = set(unique(data['earned_rewards']))
    if not earned <= {r.source_id for r in reward_catalog()}:
        raise ValueError('Unknown earned reward source')
    for resource in ('rerolls', 'extras'):
        acquired = integer(data[resource + '_earned'], 0, 7)
        used = integer(data[resource + '_used'], 0, acquired)
        if integer(data[resource], 0, 7) != acquired - used:
            raise ValueError('Available resources must equal earned marks minus crossed marks')
    return PlayerSheet(set(yellow), blue, green, list(orange), list(purple), earned,
                       data['rerolls'], data['extras'], integer(data['emblem_count']),
                       data['rerolls_used'], data['extras_used'])


def restore_stack(data):
    return BonusStack.restore(data)


def _validate_phase(state, players):
    """A saved engine must be stopped at a real choice or completed barrier."""
    phase, active = state.phase, state.active
    windows = state.windows
    simultaneous = phase in ('passive', 'round_bonus')
    if simultaneous != (state.frozen_sheets is not None):
        raise ValueError('Frozen sheets must match a simultaneous window')
    if phase != 'regular' and state.available:
        raise ValueError('Finished regular play cannot retain available dice')
    if state.resigned is not None and phase != 'over':
        raise ValueError('Only a finished game can record a surrender')
    if phase != 'over' and active != (state.starting + state.turn_index) % players:
        raise ValueError('Active seat does not match the turn schedule')
    if phase in ('regular', 'active_extra'):
        if not 1 <= state.regular_roll <= 3:
            raise ValueError('Invalid regular opportunity')
        for player, window in enumerate(windows):
            if player != active and (window.stage != 'done' or not window.stack.is_empty or window.extra_used):
                raise ValueError('Only the active player can have an active-turn window')
        window = windows[active]
        if phase == 'regular':
            if window.stage not in ('regular', 'after_regular') or window.extra_used:
                raise ValueError('Invalid active regular window')
            if (window.stage == 'after_regular' and window.stack.is_empty) or (window.stage == 'regular' and not window.stack.is_empty):
                raise ValueError('Regular bonus chain does not match its return stage')
            if window.stage == 'regular' and not state.available:
                raise ValueError('Regular roll requires available dice')
        elif window.stage != 'extra':
            raise ValueError('Active extra phase requires an extra-die window')
        eligible = [active]
    elif phase == 'passive':
        if any(w.stage not in ('passive', 'extra', 'done') or
               (w.stage != 'extra' and not w.stack.is_empty) for w in windows):
            raise ValueError('Invalid passive player window')
        if players > 1:
            if windows[active].stage != 'done' or windows[active].extra_used:
                raise ValueError('Active player cannot submit in a passive window')
            if state.frozen_sheets[active] != sheet_view(state.sheets[active]):
                raise ValueError('Active sheet changed within a passive window')
            if not 1 <= state.regular_roll <= 3:
                raise ValueError('Invalid passive regular opportunity')
        elif state.regular_roll != 0 or state.reroll_no != 0 or len(state.tray) != 3 or len(state.kept) != 3:
            raise ValueError('Invalid simulated passive roll')
        eligible = [p for p,w in enumerate(windows) if w.stage != 'done']
    elif phase == 'round_bonus':
        if state.round_no != 4 or state.turn_index != 0:
            raise ValueError('Round bonus phase must begin round four')
        if any(w.stage not in ('round', 'done') or w.extra_used or
               (w.stage == 'round' and not w.stack.is_empty) for w in windows):
            raise ValueError('Invalid round-bonus player window')
        eligible = [p for p,w in enumerate(windows) if w.stage != 'done' or not w.stack.is_empty]
    elif state.resigned is not None:
        # A surrender ends the game at any point of the schedule; nothing may remain open.
        if any(w.stage != 'done' or not w.stack.is_empty or w.extra_used for w in windows):
            raise ValueError('A surrendered game still has unfinished player windows')
        return
    else:
        if state.round_no != load_sheet().round_counts[players-1] or state.turn_index != 0:
            raise ValueError('Completed game must finish its last round')
        if active != (state.starting + players-1) % players or any(w.stage != 'done' or not w.stack.is_empty for w in windows):
            raise ValueError('Completed game still has unfinished player windows')
        return
    if not eligible or any(not window_actions(state, player) for player in eligible):
        raise ValueError('Snapshot contains an unfinished automatic transition')


def state_snapshot(state):
    return {'starting':state.starting,'active':state.active,'round_no':state.round_no,'turn_index':state.turn_index,
            'phase':state.phase,'dice':dict(state.dice),'available':list(state.available),'kept':list(state.kept),'tray':list(state.tray),
            'regular_roll':state.regular_roll,'reroll_no':state.reroll_no,
            'sheets':[sheet_view(sheet) for sheet in state.sheets],
            'windows':[{'stage':w.stage,'extra_used':sorted(w.extra_used),'stack':w.stack.snapshot()} for w in state.windows],
            'frozen_sheets':None if state.frozen_sheets is None else _copy_json(state.frozen_sheets),
            # Only a surrendered game carries the key, so every other snapshot keeps its exact shape.
            **({'resigned':state.resigned} if state.resigned is not None else {})}


def restore_state(data, players):
    keys(data, ('starting','active','round_no','turn_index','phase','dice','available','kept','tray','regular_roll','reroll_no','sheets','windows','frozen_sheets')
         + (('resigned',) if 'resigned' in data else ()))
    resigned = integer(data['resigned'],0,players-1) if 'resigned' in data else None
    starting, active = (integer(data[key],0,players-1) for key in ('starting','active'))
    integer(data['round_no'],1,load_sheet().round_counts[players-1])
    integer(data['turn_index'],0,players-1)
    if data['phase'] not in ('regular','active_extra','passive','round_bonus','over'):
        raise ValueError('Invalid phase in game snapshot')
    keys(data['dice'], DICE)
    dice = {die:integer(value,1,6) for die,value in data['dice'].items()}
    zones = [unique(data[key]) for key in ('available','kept','tray')]
    flattened = [die for zone in zones for die in zone]
    if len(flattened) != 6 or set(flattened) != set(DICE):
        raise ValueError('Dice zones must partition the six physical dice')
    sheets = [restore_sheet(sheet) for sheet in sequence(data['sheets'])]
    windows = []
    for item in sequence(data['windows']):
        keys(item, ('stage','extra_used','stack'))
        if item['stage'] not in ('regular','after_regular','round','passive','extra','done'):
            raise ValueError('Invalid player window stage')
        used = set(unique(item['extra_used']))
        if not used <= set(DICE):
            raise ValueError('Invalid extra-die identity')
        windows.append(PlayerWindow(item['stage'],used,restore_stack(item['stack'])))
    if len(sheets) != players or len(windows) != players:
        raise ValueError('Invalid player state count')
    frozen = data['frozen_sheets']
    if frozen is not None:
        if len(sequence(frozen)) != players:
            raise ValueError('Invalid frozen sheet count')
        frozen = [sheet_view(restore_sheet(sheet)) for sheet in frozen]
    state = TurnState(starting,active,sheets,windows,data['round_no'],data['turn_index'],data['phase'],dice,
                      list(zones[0]),list(zones[1]),list(zones[2]),integer(data['regular_roll'],0,3),
                      integer(data['reroll_no']),frozen,resigned)
    _validate_phase(state, players)
    return state


def event_data(event):
    return {'sequence':event.sequence,'kind':event.kind,'payload':_copy_json(event.payload)}


def event_restore(data):
    keys(data, ('sequence','kind','payload'))
    if not isinstance(data['kind'], str) or not isinstance(data['payload'],dict):
        raise ValueError('Invalid event in delivery receipt')
    return Event(integer(data['sequence'],1),data['kind'],data['payload'])


def receipt_snapshot(receipt):
    return {'events':[event_data(e) for e in receipt['events']],
            'projected':{key:[event_data(e) for e in events] for key,events in receipt['projected'].items()}}


def receipt_restore(data, players):
    keys(data, ('events','projected'))
    keys(data['projected'], [str(p) for p in range(players)] + ['observer'])
    result = {'events':tuple(event_restore(e) for e in sequence(data['events'])),
              'projected':{key:tuple(event_restore(e) for e in sequence(events)) for key,events in data['projected'].items()}}
    for events in (result['events'],*result['projected'].values()):
        if len(events) > 512 or any(right.sequence != left.sequence+1 for left,right in zip(events,events[1:])):
            raise ValueError('Invalid bounded event receipt')
    return result


def snapshot(engine) -> dict[str, JsonValue]:
    return {'format':2,'game_id':'dice_circuit','rules_version':'1','engine_version':'1','randomness_version':'1',
            'config':{'game_id':engine.config.game_id,'variant':engine.config.variant,'players':engine.config.players},
            'seed':engine._seed,'future_seed':engine._future_seed,'forked':engine._forked,'state':state_snapshot(engine._state),
            'presentation':{'sequence':engine._sequence,'revisions':list(engine._revisions),
                            'cursors':list(engine._cursors),'decisions':list(engine._decisions)},
            'bootstrap':receipt_snapshot(engine._bootstrap),'receipt':receipt_snapshot(engine._receipt)}


def load(engine, data):
    data = _copy_json(data)
    if not isinstance(data, dict) or type(data.get('format')) is not int or data['format'] not in (1, 2):
        raise ValueError('Unsupported Dice Circuit snapshot version')
    fields = ('format','game_id','rules_version','engine_version','randomness_version','config','seed','state','presentation','bootstrap','receipt')
    keys(data, fields + (('future_seed','forked') if data['format'] == 2 else ()))
    if any(data[k] != '1' for k in ('rules_version','engine_version','randomness_version')) or data['game_id'] != 'dice_circuit':
        raise ValueError('Unsupported Dice Circuit snapshot version')
    keys(data['config'], ('game_id','variant','players'))
    config = GameConfig(**data['config'])
    engine._validate_config(config)
    KeyedRandom(data['seed'])
    future_seed = data['future_seed'] if data['format'] == 2 else data['seed']
    forked = data['forked'] if data['format'] == 2 else False
    if type(forked) is not bool or (not forked and future_seed != data['seed']):
        raise ValueError('Invalid future chance lineage')
    random = KeyedRandom(future_seed)
    state = restore_state(data['state'], config.players)
    p = data['presentation']
    keys(p, ('sequence','revisions','cursors','decisions'))
    vectors = [[integer(v) for v in sequence(p[key])] for key in ('revisions','cursors','decisions')]
    if [len(v) for v in vectors] != [config.players+1,config.players+1,config.players]:
        raise ValueError('Invalid perspective metadata count')
    engine.config, engine._seed, engine._random, engine._state = config, data['seed'], random, state
    engine._future_seed, engine._forked = future_seed, forked
    engine._sequence = integer(p['sequence'])
    engine._revisions, engine._cursors, engine._decisions = vectors
    engine._bootstrap = receipt_restore(data['bootstrap'], config.players)
    engine._receipt = receipt_restore(data['receipt'], config.players)
    if not engine._bootstrap['events'] or engine._bootstrap['events'][0].sequence != 1:
        raise ValueError('Missing bootstrap receipt')
    if not engine._receipt['events'] or engine._receipt['events'][-1].sequence != engine._sequence:
        raise ValueError('Current event receipt does not match event position')
    if engine._bootstrap['events'][-1].sequence > engine._sequence:
        raise ValueError('Bootstrap receipt exceeds event position')
    for viewer in (*range(config.players),None):
        key = 'observer' if viewer is None else str(viewer)
        cursor = engine._cursors[engine._viewer_index(viewer)]
        current = engine._receipt['projected'][key]
        if current and current[-1].sequence != cursor:
            raise ValueError('Current projection does not match viewer cursor')
        if engine._bootstrap['projected'][key] and engine._bootstrap['projected'][key][-1].sequence > cursor:
            raise ValueError('Bootstrap projection exceeds viewer cursor')
    engine._events, engine._audiences = [], []
