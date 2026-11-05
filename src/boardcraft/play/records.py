"""Versioned trusted-local recordings, using atomic gzip JSON persistence."""
from dataclasses import asdict, dataclass, field
import gzip
import json
import os
import stat
from pathlib import Path
import tempfile
import zlib
from boardcraft.contracts import Action, BotReference, Decision, Event, GameConfig, GameOutcome, _copy_json, config_data, decision_meta
from boardcraft.inference.catalog import display_name as safe_name, reference_data, reference_from_data


@dataclass(frozen=True)
class ParticipantRecord:
    seat: int
    kind: str
    bot_ref: BotReference | None = None
    display_name: str | None = None

    def __post_init__(self):
        if type(self.seat) is not int or self.seat < 0 or self.kind not in ('human', 'builtin', 'model', 'external', 'unknown'):
            raise ValueError('Invalid game record participant')
        if self.display_name is not None:
            safe_name(self.display_name)
        if self.kind in ('builtin', 'model'):
            if not isinstance(self.bot_ref, BotReference) or self.kind != self.bot_ref.kind:
                raise ValueError('Invalid participant bot reference')
            reference_data(self.bot_ref)
        elif self.bot_ref is not None or (self.kind == 'unknown' and self.display_name is not None):
            raise ValueError('Invalid participant identity declaration')


def participants_from_seats(seats):
    return tuple(ParticipantRecord(seat, 'human' if item['kind'] == 'human' else item['bot_ref']['kind'],
        None if item['kind'] == 'human' else reference_from_data(item['bot_ref']), item['display_name'])
        for seat, item in enumerate(seats))


def participant_data(participant: ParticipantRecord) -> dict:
    """Serialize either bot reference family with its canonical public shape."""
    return dict(seat=participant.seat, kind=participant.kind,
                bot_ref=None if participant.bot_ref is None else reference_data(participant.bot_ref),
                display_name=participant.display_name)


def validate_participants(participants, config, rules_version):
    if not isinstance(participants, tuple) or len(participants) != config.players:
        raise ValueError('Invalid game record participant count')
    for seat, item in enumerate(participants):
        if not isinstance(item, ParticipantRecord) or item.seat != seat:
            raise ValueError('Invalid game record participant order')
        if item.bot_ref is not None:
            reference_data(item.bot_ref)
            compat = item.bot_ref.compatibility
            if compat.game_id != config.game_id or compat.rules_version != rules_version:
                raise ValueError('Incompatible game record participant')
    return participants


@dataclass(frozen=True)
class RecordedDecision:
    decision: Decision
    action: Action
    events: tuple[Event, ...]
    explanation: str
    meta: dict | None = field(default=None, kw_only=True)

    def __post_init__(self):
        object.__setattr__(self, "meta", decision_meta(self.meta))


@dataclass(frozen=True)
class RecordedOperation:
    actor: int
    decision: Decision | None
    before_revision: int
    action: Action
    events: tuple[Event, ...]
    explanation: str
    meta: dict | None = field(default=None, kw_only=True)

    def __post_init__(self):
        object.__setattr__(self, "meta", decision_meta(self.meta))


@dataclass(frozen=True)
class GameRecord:
    version: str
    config: GameConfig
    rules_version: str
    engine_version: str
    initial_snapshot: dict
    initial_events: tuple[Event, ...]
    decisions: tuple[RecordedDecision, ...]
    complete: bool
    final_result: GameOutcome | None
    participants: tuple[ParticipantRecord, ...] = ()
    operations: tuple[RecordedOperation, ...] = ()

    @property
    def steps(self):
        return self.operations if self.version == '3' else self.decisions

    def __post_init__(self):
        object.__setattr__(self, 'initial_snapshot', _copy_json(self.initial_snapshot))
        if self.version == '1' and not self.participants:
            object.__setattr__(self, 'participants', tuple(ParticipantRecord(seat, 'unknown') for seat in range(self.config.players)))


def _object(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError('Invalid game record fields')
    return value


def _text(value):
    if not isinstance(value, str) or not value:
        raise ValueError('Invalid game record text')
    return value


def _integer(value, minimum=None):
    if type(value) is not int or (minimum is not None and value < minimum):
        raise ValueError('Invalid game record integer')
    return value


def _list(value):
    if not isinstance(value, list):
        raise ValueError('Invalid game record list')
    return value


def _event(data):
    _object(data, ('sequence', 'kind', 'payload'))
    if not isinstance(data['payload'], dict):
        raise ValueError('Invalid game record event payload')
    return Event(_integer(data['sequence'], 1), _text(data['kind']), data['payload'])


def _decode_step(item, players, operations):
    _object(item, ('decision', 'action', 'events', 'explanation') + (('actor', 'before_revision') if operations else ())
            + (('meta',) if isinstance(item, dict) and 'meta' in item else ()))
    meta = decision_meta(item.get('meta'))
    decision = None
    if not operations or item['decision'] is not None:
        d = _object(item['decision'], ('id', 'player', 'kind'))
        decision = Decision(_text(d['id']), _integer(d['player'], 0), _text(d['kind']))
        if decision.player >= players:
            raise ValueError('Invalid game record decision owner')
    a = _object(item['action'], ('id', 'kind', 'params'))
    if not isinstance(a['params'], dict):
        raise ValueError('Invalid game record action parameters')
    selected = Action(_text(a['id']), _text(a['kind']), a['params'])
    receipt = tuple(_event(e) for e in _list(item['events']))
    explanation = _text(item['explanation'])
    if operations:
        actor = _integer(item['actor'], 0)
        if actor >= players or (decision is not None and decision.player != actor):
            raise ValueError('Invalid game record operation owner')
        return RecordedOperation(actor, decision, _integer(item['before_revision'], 0),
                                 selected, receipt, explanation, meta=meta)
    return RecordedDecision(decision, selected, receipt, explanation, meta=meta)


def _decode(data, *, module=None):
    if not isinstance(data, dict) or data.get('version') not in ('1', '2', '3'):
        raise ValueError('Unsupported game record version')
    version = data['version']
    operations = version == '3'
    step_key = 'operations' if operations else 'decisions'
    _object(data, ('version', 'config', 'rules_version', 'engine_version', 'initial_snapshot',
                   'initial_events', step_key, 'complete', 'final_result') + (() if version == '1' else ('participants',)))
    config = _object(data['config'], ('game_id', 'variant', 'players') + (('settings',) if operations else ()))
    settings = config.get('settings', {})
    if type(settings) is not dict:
        raise ValueError('Invalid game record settings')
    config = GameConfig(_text(config['game_id']), _text(config['variant']), _integer(config['players'], 1), settings)
    if operations:
        from boardcraft.registry import get_game
        from boardcraft.settings import normalize_settings
        module = module or get_game(config.game_id)
        normalize_settings(module.settings_schema(), config.settings)
    if not isinstance(data['initial_snapshot'], dict) or type(data['complete']) is not bool:
        raise ValueError('Invalid game record initial snapshot or completion marker')
    steps = tuple(_decode_step(item, config.players, operations) for item in _list(data[step_key]))
    final = None
    if data['final_result'] is not None:
        if operations:
            decoder = module
        else:
            from boardcraft.games.dice_circuit.module import DiceCircuitModule
            decoder = DiceCircuitModule()
        final = decoder.decode_result(data['final_result'], config.players)
    if data['complete'] != (final is not None):
        raise ValueError('Game record completion marker disagrees with final result')
    if version == '1':
        participants = tuple(ParticipantRecord(seat, 'unknown') for seat in range(config.players))
    else:
        items = []
        for item in _list(data['participants']):
            _object(item, ('seat', 'kind', 'bot_ref', 'display_name'))
            items.append(ParticipantRecord(item['seat'], item['kind'],
                         None if item['bot_ref'] is None else reference_from_data(item['bot_ref']), item['display_name']))
        participants = validate_participants(tuple(items), config, data['rules_version'])
    return GameRecord(data['version'], config, _text(data['rules_version']), _text(data['engine_version']),
                      _copy_json(data['initial_snapshot']), tuple(_event(e) for e in _list(data['initial_events'])),
                      () if operations else steps, data['complete'], final, participants,
                      steps if operations else ())


def record_data(record):
    """Return ordinary JSON data for controlled local storage only."""
    if not isinstance(record, GameRecord):
        raise ValueError('Expected a game record')
    data = asdict(record)
    data['config'] = config_data(record.config)
    if record.version == '3':
        if record.decisions:
            raise ValueError('Operation records cannot include a second history')
        data.pop('decisions')
        data['config']['settings'] = _copy_json(record.config.settings)
    else:
        if record.operations:
            raise ValueError('Legacy records cannot include operations')
        data.pop('operations')
    for step in data['operations' if record.version == '3' else 'decisions']:
        if step['meta'] is None:
            step.pop('meta')
    data['participants'] = [participant_data(item) for item in record.participants]
    if record.version == '1':
        if record.participants != tuple(ParticipantRecord(seat, 'unknown') for seat in range(record.config.players)):
            raise ValueError('Historical game record participants must be unknown')
        data.pop('participants')
    return json.loads(json.dumps(data, allow_nan=False))


def validate_record(record, *, module=None):
    try:
        return _decode(record_data(record), module=module)
    except (TypeError, KeyError, AttributeError) as error:
        raise ValueError('Invalid game record') from error


def _file_identity(info):
    if not stat.S_ISREG(info.st_mode) or info.st_dev < 0 or info.st_ino <= 0:
        raise ValueError('Regular file identity is unavailable')
    return {'device': int(info.st_dev), 'inode': int(info.st_ino)}


def write_record(record, path, *, temporary_path=None, on_created=None) -> None:
    data = record_data(validate_record(record))
    destination = Path(path)
    temporary = None
    created = False
    proof = None
    if on_created is not None and temporary_path is None:
        raise ValueError('Owned record writes require their reserved temporary path')
    try:
        if temporary_path is None:
            descriptor, temporary = tempfile.mkstemp(prefix=destination.name + '.', suffix='.tmp', dir=destination.parent)
        else:
            temporary = Path(temporary_path)
            if temporary.parent != destination.parent or temporary == destination:
                raise ValueError('Atomic record temporary must be a distinct sibling')
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        created = True
        with os.fdopen(descriptor, 'wb') as stream:
            if on_created is not None:
                proof = os.dup(stream.fileno())
                on_created('temporary', Path(temporary), _file_identity(os.fstat(proof)))
            with gzip.GzipFile(filename='', fileobj=stream, mode='wb', mtime=0) as compressed:
                compressed.write(json.dumps(data, separators=(',', ':'), sort_keys=record.version == '3',
                                            allow_nan=False).encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        if on_created is None:
            os.replace(temporary, destination)
        else:
            # Exclusive hardlink publication never overwrites a reserved collision.
            os.link(temporary, destination)
            on_created('stage', destination, _file_identity(os.stat(destination, follow_symlinks=False)))
    finally:
        try:
            if created and temporary is not None and os.path.exists(temporary):
                if on_created is None:
                    os.unlink(temporary)
                elif proof is not None:
                    # The live descriptor prevents inode reuse during local cleanup.
                    try:
                        if _file_identity(os.stat(temporary, follow_symlinks=False)) == _file_identity(os.fstat(proof)):
                            os.unlink(temporary)
                    except (OSError, ValueError):
                        pass  # Preserve an unproven replacement; durable owner reports it.
        finally:
            if proof is not None:
                os.close(proof)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field in game record')
        result[key] = value
    return result


def read_record(path) -> GameRecord:
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            data = json.load(stream, object_pairs_hook=_unique_object,
                             parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite game record number')))
        return _decode(_copy_json(data))
    except (OSError, EOFError, UnicodeError, json.JSONDecodeError, TypeError, KeyError, zlib.error) as error:
        raise ValueError('Cannot read a valid gzip JSON game record') from error
