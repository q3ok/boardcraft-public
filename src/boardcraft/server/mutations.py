"""Optimistic game computation with short, atomic compare-and-swap commits."""
from dataclasses import asdict, dataclass
import hashlib
import json
from boardcraft.contracts import Action, GameConfig, IllegalAction
from . import decision_clock, directory
from .storage import digest, encode
from .views import AccessDenied, Conflict, creator_for, player_view, viewer_for
from .idempotency import idempotent, remember


@dataclass(frozen=True)
class PreparedMove:
    table: str
    expected_revision: int
    revision: int
    payload: str
    checksum: str
    step: int
    record: str
    record_checksum: str
    journals: list
    view: dict
    directory: directory.PreparedSummary | None
    progress: tuple | None = None


@dataclass(frozen=True)
class PreparedStart:
    table: str
    expected_revision: int
    revision: int
    payload: str
    checksum: str
    bootstrap: str
    bootstrap_checksum: str
    journals: list
    view: dict
    directory: directory.PreparedSummary


class Mutations:
    def _read_state(self, table):
        with self.store.transaction() as db:
            state = self.store.load(db, table)
        return self._migrate_seats(table, state)

    @idempotent
    def start_game(self, table_id, credential):
        state = self._read_state(table_id)
        self._creator(state, credential)
        return self._start_table(table_id, state)

    def autostart_if_ready(self, table_id):
        """The server's own start of a table whose creator asked for it, once every seat is ready. Idempotent:
        a table that is not waiting, not armed or not ready is left alone. Returns True when it started the table."""
        state = self._read_state(table_id)
        if state['status'] != 'lobby' or not state.get('autostart'):
            return False
        from .views import seats_view
        seats = seats_view(state)
        if not seats or not all(s['occupied'] and (s.get('availability') or {}).get('status') != 'unavailable' for s in seats):
            return False
        try:
            self._start_table(table_id, state)
        except Conflict:
            return False  # someone else started it or the lobby changed; the next look decides again
        return True

    def _start_table(self, table_id, state):
        if state['status'] != 'lobby':
            raise Conflict('Table has already started')
        if any(item['kind'] == 'human' and item['hash'] is None for item in state['seats']):
            raise Conflict('All human seats must be occupied and ready')
        self._resolve_seats(state)
        module = self.module(state['config']['game_id'])
        engine = module.new(GameConfig(**state['config']), state['seed'])
        for seat, item in enumerate(state["seats"]):
            if (item.get("bot_ref") or {}).get("kind") == "model" or (item.get("bot_ref") or {}).get("builtin_kind") == "hybrid":
                from boardcraft.inference.catalog import reference_from_data
                seed = int.from_bytes(hashlib.sha256(f"{state['seed']}:{seat}".encode()).digest()[:8], "big")
                bot = self._provider_resolve(reference_from_data(item["bot_ref"]), seed)
                initialize = getattr(bot, "initialize", None)
                if callable(initialize):
                    initialize(engine.observe(seat))
                    item["bot_state"] = bot.save_state()
        receipt = engine.initial_events()
        journals = self.store.project_journals(table_id, engine, receipt, state['cursors'])
        expected = state['revision']
        state['revision'] += 1
        state['snapshot'] = engine.snapshot()
        state['status'] = 'completed' if engine.is_over() else 'playing'
        decision_clock.arm(state, engine, self.clock())
        from .envelope import advance_perspectives
        advance_perspectives(table_id, state, engine, module)
        from boardcraft.play.records import participant_data, participants_from_seats
        # A migrated historical active record must remain honestly unknown.
        version = {}
        if state.get('record_version') == '3':
            version = {'record_version': '3', 'rules_version': module.rules_version,
                       'engine_version': module.engine_version}
        initial = encode({**version, 'snapshot': state['snapshot'], 'events': [asdict(event) for event in receipt],
                          'participants': [participant_data(item) for item in participants_from_seats(state['seats'])]})
        payload = encode(state)
        view = self._envelope(table_id, state, engine, None)
        prepared = PreparedStart(table_id, expected, state['revision'], payload, digest(payload),
                                 initial, digest(initial), journals, view, directory.prepare_summary(table_id, state, engine=engine, rules_version=module.rules_version, engine_version=module.engine_version))
        if not self._commit_start(prepared):
            raise Conflict('Lobby changed; read the current table and retry')
        return prepared.view

    def _commit_start(self, prepared):
        with self.store.transaction(True) as db:
            changed = db.execute('UPDATE tables SET payload=?, checksum=?, revision=? WHERE table_id=? AND revision=?',
                                 (prepared.payload, prepared.checksum, prepared.revision, prepared.table, prepared.expected_revision))
            if changed.rowcount != 1:
                return False
            directory.write_summary(db, prepared.table, prepared.directory)
            db.execute('INSERT INTO bootstrap VALUES (?, ?, ?)',
                       (prepared.table, prepared.bootstrap, prepared.bootstrap_checksum))
            db.executemany('INSERT INTO journals VALUES (?, ?, ?, ?, ?)', prepared.journals)
            remember(db, self.store, prepared.view)
        return True

    def _prepare_apply(self, table, state, engine, seat, decision_id, action_id, explanation,
                       *, selected=None, seat_operation=False, meta=None):
        if engine is None or state['status'] != 'playing':
            raise Conflict('Table is not accepting actions')
        observation = engine.observe(seat)
        from .envelope import advance_perspectives
        module = self.module(state['config']['game_id'])
        advance_perspectives(table, state, engine, module)
        decision = None if seat_operation else observation.decision
        if not seat_operation and (decision is None or decision.id != decision_id):
            raise Conflict('Decision is stale or unavailable; read legal actions again.', code='stale_decision', argument='decision_id')
        if selected is None:
            selected = next((action for action in engine.legal_actions(seat) if action.id == action_id), None)
        if selected is None:
            raise Conflict('Action is not legal for this decision; select a current legal action ID.', code='illegal_action', argument='action_id')
        off_turn = seat_operation and observation.decision is None
        round_before = directory.table_round(engine, module)
        pending = engine.decision() if off_turn and selected.kind == 'request_trade' else None
        try:
            if seat_operation:
                receipt = engine.apply_seat_operation(seat, selected, observation.revision)
            else:
                receipt = engine.apply(seat, selected, decision_id)
        except IllegalAction as error:
            raise Conflict('Action is no longer available; read legal actions again.', code='illegal_action', argument='action_id') from error
        journals = self.store.project_journals(table, engine, receipt, state['cursors'])
        fields = ({'actor': seat, 'before_revision': observation.revision}
                  if state.get('record_version') == '3' else {})
        from boardcraft.contracts import decision_meta
        meta = decision_meta(meta)
        if meta is not None:
            fields["meta"] = meta
        record = encode({**fields, 'decision': None if decision is None else asdict(decision), 'action': asdict(selected),
                         'events': [asdict(event) for event in receipt], 'explanation': explanation})
        expected = state['revision']
        state['revision'] += 1
        state['steps'] += 1
        state['snapshot'] = engine.snapshot()
        state['status'] = 'completed' if engine.is_over() else 'playing'
        decision_clock.arm(state, engine, self.clock(), preserve=off_turn, pending_request_decision=pending)
        advance_perspectives(table, state, engine, module, actor=seat)
        payload = encode(state)
        view = self._envelope(table, state, engine, seat)
        return PreparedMove(table, expected, state['revision'], payload, digest(payload),
                            state['steps'], record, digest(record), journals, view,
                            directory.prepare_summary(table, state, engine=engine,
                                rules_version=self.module(state['config']['game_id']).rules_version,
                                engine_version=self.module(state['config']['game_id']).engine_version) if state['status'] == 'completed' else None,
                            directory.prepare_round(self.store, table, progress)
                            if state['status'] == 'playing' and (progress := directory.table_round(engine, module)) and progress != round_before else None)

    def _commit_apply(self, prepared):
        with self.store.transaction(True) as db:
            changed = self._write_move(db, prepared)
            if changed:
                remember(db, self.store, prepared.view)
            return changed

    @staticmethod
    def _write_move(db, prepared):
        changed = db.execute('UPDATE tables SET payload=?, checksum=?, revision=? WHERE table_id=? AND revision=?',
                             (prepared.payload, prepared.checksum, prepared.revision, prepared.table, prepared.expected_revision))
        if changed.rowcount != 1:
            return False
        if prepared.directory is not None:
            directory.write_summary(db, prepared.table, prepared.directory)
        elif prepared.progress is not None:
            directory.write_round(db, prepared.table, prepared.progress)
        db.execute('INSERT INTO steps VALUES (?, ?, ?, ?)',
                   (prepared.table, prepared.step, prepared.record, prepared.record_checksum))
        db.executemany('INSERT INTO journals VALUES (?, ?, ?, ?, ?)', prepared.journals)
        return True

    @idempotent
    def apply_action(self, table_id, credential, decision_id, action_id, *, decision_token=None):
        for _ in range(16):
            state = self._read_state(table_id)
            seat = viewer_for(state, credential)
            if seat is None:
                raise AccessDenied('Seat credential required to act', code='credential_required')
            engine = self._engine(state)
            from .envelope import validate_token
            if decision_token is not None:
                validate_token(decision_token, self._envelope(table_id, state, engine, seat)['decision_token'])
                own_decision = engine.observe(seat).decision
                decision_id = None if own_decision is None else own_decision.id
            state['seats'][seat].pop('autopilot', None)
            state['seats'][seat].pop('autopilot_round', None)
            prepared = self._prepare_apply(table_id, state, engine, seat, decision_id, action_id, 'Player action')
            if self._commit_apply(prepared):
                return prepared.view
        raise Conflict('Table is busy; read your current decision and retry')

    def apply_bot(self, work, choice):
        from .bots import StatefulChoice, unchanged
        if work.expected_revision is not None and (not isinstance(choice, StatefulChoice) or choice.work is not work):
            return False
        for _ in range(16):
            state = self._read_state(work.table_id)
            engine = self._engine(state)
            if not unchanged(work, state, engine):
                return False
            if work.expected_revision is not None:
                state['seats'][work.seat]['bot_state'] = choice.state_after
            elif choice.action not in work.actions:
                return False
            prepared = self._prepare_apply(work.table_id, state, engine, work.seat,
                                           work.observation.decision.id, choice.action.id, choice.explanation,
                                           selected=choice.action, meta=choice.meta)
            if self._commit_apply(prepared):
                return True
            if work.expected_revision is not None:
                return False
        return False

    def _change_control(self, table, state, engine, seat, autopilot):
        expected = state['revision']
        state['seats'][seat]['autopilot'] = autopilot
        # The round the autopilot took over in, so the player reads "You ran out of time in round N".
        found = directory.table_round(engine, self.module(state['config']['game_id'])) if autopilot else None
        if found:
            state['seats'][seat]['autopilot_round'] = found['round']
        current = engine.decision()
        if current is not None and current.player == seat:
            decision_clock.arm(state, engine, self.clock())
        state['revision'] += 1
        payload = encode(state)
        with self.store.transaction(True) as db:
            changed = db.execute('UPDATE tables SET payload=?,checksum=?,revision=? WHERE table_id=? AND revision=?',
                                 (payload, digest(payload), state['revision'], table, expected)).rowcount == 1
            if changed:
                remember(db, self.store, self._envelope(table, state, engine, seat))
            return changed

    @idempotent
    def submit_command(self, table_id: str, credential: str, expected_revision: int, kind: str,
                       params: dict, decision_id: str | None = None, *, decision_token=None) -> dict:
        for _ in range(16):
            result = self._submit_command_once(table_id, credential, expected_revision, kind, params, decision_id,
                                               decision_token=decision_token)
            if result is not None:
                return result
            if decision_token is None:
                break
        raise Conflict('Command is stale or unavailable')

    def _submit_command_once(self, table_id, credential, expected_revision, kind, params, decision_id, *, decision_token):
        state = self._read_state(table_id)
        seat = viewer_for(state, credential)
        if seat is None:
            raise AccessDenied('Seat credential required to act', code='credential_required')
        engine = self._engine(state)
        from .envelope import validate_token
        if decision_token is not None:
            validate_token(decision_token, self._envelope(table_id, state, engine, seat)['decision_token'])
        specs = decision_clock.commands(state, engine, seat)
        spec = next((s for s in specs if s['kind'] == kind), None)
        if spec is None or (decision_token is None and (type(expected_revision) is not int
                                                        or expected_revision != decision_clock.command_revision(state, engine, seat))):
            raise Conflict('Command is stale or unavailable')
        if type(params) is not dict or set(params) - spec['params_schema']['properties'].keys():
            raise ValueError('Invalid command parameters')
        seat_operation = spec['scope'] == 'seat'
        if decision_token is not None and not seat_operation:
            own_decision = engine.observe(seat).decision
            decision_id = None if own_decision is None else own_decision.id
        if seat_operation and decision_id is not None:
            raise Conflict('A seat command has no decision ID')
        own_decision = engine.observe(seat).decision
        if not seat_operation and (own_decision is None or own_decision.id != decision_id):
            raise Conflict('Decision is stale or unavailable; read legal actions again.', code='stale_decision', argument='decision_id')
        if kind == 'reclaim':
            if not self._change_control(table_id, state, engine, seat, False):
                raise Conflict('Control has changed')
            return self._envelope(table_id, state, engine, seat)
        state['seats'][seat].pop('autopilot', None)
        state['seats'][seat].pop('autopilot_round', None)
        if kind == 'liquidate':
            return self._liquidate(table_id, state, engine, seat)
        prepared = self._prepare_apply(table_id, state, engine, seat, decision_id, kind, 'Player command',
                                       selected=Action(kind, kind, params), seat_operation=seat_operation)
        if not self._commit_apply(prepared):
            return None
        return prepared.view

    def _liquidate(self, table, state, engine, seat):
        moves = []
        for action in decision_clock.liquidation_actions(engine, seat):
            moves.append(self._prepare_apply(table, state, engine, seat, engine.decision().id, action.id,
                                             'Bank liquidation', selected=action))
        if not moves:
            raise Conflict('Nothing is available for bank liquidation')
        with self.store.transaction(True) as db:
            for move in moves:
                if not self._write_move(db, move):
                    raise Conflict('The debt has changed')
            remember(db, self.store, moves[-1].view)
        return moves[-1].view

    def tick(self, table_id, expected_revision=None):
        state = self._read_state(table_id)
        if (state['status'] != 'playing' or not state.get('live')
                or expected_revision is not None and expected_revision != state['revision']):
            return False
        deadline = state.get('deadline')
        now = self.clock()
        if deadline:
            if now < deadline['at']:
                return False
        elif (state.get('automatic_at') is None or now < state['automatic_at']
              or not any(item.get('autopilot') for item in state['seats'])):
            return False
        engine = self._engine(state)
        decision = engine.decision()
        seat = decision.player
        if deadline:
            if deadline['seat'] != seat or deadline['decision_id'] != decision.id:
                return False
            effect = deadline['effect']
            if effect == 'autopilot':
                return self._change_control(table_id, state, engine, seat, True)
            action = Action(effect, effect, {})
            reason = 'Trade window expired' if deadline['kind'] == 'window' else 'Decision timeout'
        elif state['seats'][seat].get('autopilot'):
            action = decision_clock.passive_action(engine, decision)
            reason = 'Autopilot'
        else:
            return False
        move = self._prepare_apply(table_id, state, engine, seat, decision.id, action.id, reason,
                                   selected=action, seat_operation=action.kind == 'resign')
        return self._commit_apply(move)
