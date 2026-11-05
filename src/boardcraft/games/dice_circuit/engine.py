"""Deterministic Dice Circuit engine and bounded, viewer-safe event receipts."""
from dataclasses import replace
import json
from boardcraft.contracts import Action, Decision, Event, GameConfig, IllegalAction, StaleDecision, WrongPlayer
from . import observation, persistence
from .randomness import KeyedRandom
from .scoring import rank_scores, score_sheet
from .state import PlayerSheet
from .turn_actions import window_actions
from .turn_flow import TurnFlow
from .turn_state import PlayerWindow, TurnState


class DiceCircuitEngine(TurnFlow):
    _recorded = True  # A fork(recorded=False) keeps no events, receipts or transactions.

    def __init__(self):
        self._legal = {}

    @staticmethod
    def _validate_config(config):
        if not isinstance(config, GameConfig) or config.game_id != 'dice_circuit' or config.variant != 'base' or not 1 <= config.players <= 4:
            raise ValueError('Dice Circuit supports base games with one to four players')

    @classmethod
    def new(cls, config: GameConfig, seed: int):
        cls._validate_config(config)
        engine = cls()
        engine.config, engine._seed, engine._random = config, seed, KeyedRandom(seed)
        engine._future_seed, engine._forked = seed, False
        starting = engine._random.starting_seat(config.players)
        engine._state = TurnState(starting, starting, [PlayerSheet() for _ in range(config.players)],
                                  [PlayerWindow() for _ in range(config.players)])
        engine._sequence = 0
        engine._revisions, engine._cursors = [0]*(config.players+1), [0]*(config.players+1)
        engine._decisions = [0]*config.players
        engine._events, engine._audiences = [], []
        engine._emit('game_started', {'config':{'game_id':config.game_id,'variant':config.variant,'players':config.players}, 'starting_player':starting})
        engine._start_round()
        engine._advance()
        engine._sync_decisions({}, None)
        engine._receipt = engine._finish_receipt()
        engine._bootstrap = engine._receipt
        return engine

    @classmethod
    def restore(cls, saved):
        engine = cls()
        try:
            persistence.load(engine, saved)
        except (KeyError, TypeError, IndexError, AttributeError, StopIteration, IllegalAction) as error:
            raise ValueError('Invalid Dice Circuit snapshot') from error
        return engine

    def _viewer_index(self, player):
        return self.config.players if player is None else player

    def _validate_viewer(self, player):
        if player is not None and (type(player) is not int or not 0 <= player < self.config.players):
            raise ValueError('Unknown viewer seat')

    def command_specs(self, viewer):
        """Seat operations outside a decision: any seated player may surrender while the game runs."""
        self._validate_viewer(viewer)
        if viewer is None or self.is_over():
            return ()
        return ({'kind': 'resign', 'scope': 'seat', 'label': 'Surrender: leave the game; the table ends now',
                 'params_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},)

    def apply_seat_operation(self, actor, action, expected_revision):
        """Surrender ends the game for everyone: sheets are scored as they stand and the resigning seat places last."""
        try:
            self._validate_viewer(actor)
        except ValueError as error:
            raise WrongPlayer('Unknown actor seat') from error
        if actor is None:
            raise WrongPlayer('Unknown actor seat')
        if type(expected_revision) is not int or expected_revision != self._revisions[self._viewer_index(actor)]:
            raise StaleDecision('The position has changed')
        if (self.is_over() or not isinstance(action, Action) or action.kind != 'resign' or action.id != 'resign'
                or dict(action.params) != {}):
            raise IllegalAction('Invalid seat operation')
        transaction = self.clone()
        s = transaction._state
        s.phase, s.resigned, s.frozen_sheets = 'over', actor, None
        s.tray, s.available = s.tray + s.available, []
        s.windows = [PlayerWindow() for _ in range(self.config.players)]
        transaction._emit('resigned', {'player': actor})
        transaction._emit('game_finished', {'scores': list(transaction.result().scores)})
        transaction._receipt = transaction._finish_receipt()
        self.__dict__.update(transaction.__dict__)
        return self._detached(self._receipt['events'])

    def _eligible_players(self):
        s = self._state
        if s.phase == 'over':
            return ()
        if s.phase in ('regular','active_extra'):
            return (s.active,)
        return tuple(p for p,w in enumerate(s.windows) if w.stage != 'done' or not w.stack.is_empty)

    def _decision_for(self, player):
        if player is None or player not in self._eligible_players():
            return None
        actions = self._actions_for(player)
        if not actions:
            return None
        kind = actions[0].kind
        return Decision(f'p{player}:{self._decisions[player]}',player,kind)

    def _actions_for(self, player):
        """Shared action values of the current position; a transition clears them."""
        actions = self._legal.get(player)
        if actions is None:
            actions = self._legal[player] = window_actions(self._state, player)
        return actions

    def _sync_decisions(self, previous, actor):
        for player in self._eligible_players():
            if self._decision_for(player) is not None and (player == actor or previous.get(player) is None):
                self._decisions[player] += 1

    def decision(self):
        for player in self._eligible_players():
            decision = self._decision_for(player)
            if decision is not None:
                return decision
        return None

    def observe(self, player):
        return observation.observe(self, player)

    def legal_actions(self, player):
        self._validate_viewer(player)
        if player is None or player not in self._eligible_players():
            return ()
        actions = self._actions_for(player)
        return actions if not self._recorded else tuple(Action(a.id, a.kind, a.params) for a in actions)

    def _emit(self, kind, payload, audience=None):
        if not self._recorded:
            return
        self._sequence += 1
        self._events.append(Event(self._sequence,kind,payload))
        self._audiences.append(audience)

    def _finish_receipt(self):
        projected = {}
        for viewer in (*range(self.config.players),None):
            index = self._viewer_index(viewer)
            visible = [e for e,audience in zip(self._events,self._audiences) if audience is None or audience == viewer]
            if visible:
                self._revisions[index] += 1
            projected['observer' if viewer is None else str(viewer)] = tuple(
                Event(self._cursors[index]+offset,e.kind,e.payload) for offset,e in enumerate(visible,1))
            self._cursors[index] += len(visible)
        return {'events':tuple(self._events),'projected':projected}

    @staticmethod
    def _detached(events):
        return tuple(Event(e.sequence,e.kind,e.payload) for e in events)

    def initial_events(self):
        return self._detached(self._bootstrap['events'])

    def project_events(self, events, viewer):
        """Project the exact bootstrap/current receipt, without changing state.

        A runner projects every viewer before the next accepted apply. Historical
        journals belong to the runner; cloneable state retains two bounded deltas.
        """
        self._validate_viewer(viewer)
        if not isinstance(events, tuple) or not all(isinstance(e,Event) for e in events):
            raise ValueError('Expected an exact engine event receipt')
        encoded = json.dumps([persistence.event_data(e) for e in events], sort_keys=True)
        for receipt in (self._receipt,self._bootstrap):
            expected = json.dumps([persistence.event_data(e) for e in receipt['events']], sort_keys=True)
            if encoded == expected:
                return self._detached(receipt['projected']['observer' if viewer is None else str(viewer)])
        raise ValueError('Event receipt is stale or does not match this engine')

    def apply(self, player, selected: Action, decision_id: str):
        try:
            self._validate_viewer(player)
        except ValueError as error:
            raise WrongPlayer('Unknown actor seat') from error
        current = self._decision_for(player)
        if current is None:
            raise WrongPlayer('Player does not own an eligible decision')
        if decision_id != current.id:
            raise StaleDecision('Decision is no longer current')
        # An unrecorded fork plays catalogue actions, whose id determines their parameters.
        if not isinstance(selected,Action) or not any(
            selected.id == legal.id and selected.kind == legal.kind and (not self._recorded or
            json.dumps(dict(selected.params), sort_keys=True) == json.dumps(dict(legal.params), sort_keys=True))
            for legal in self._actions_for(player)
        ):
            raise IllegalAction('Action is not legal for this decision')
        if not self._recorded:
            previous = {p:self._decision_for(p) for p in range(self.config.players)}
            self._perform(player,selected)
            self._legal = {}
            self._sync_decisions(previous,player)
            return ()
        # Commit only a fully successful transition. Caches are bounded by the
        # current receipt and bootstrap, so this copy never grows with history.
        transaction = self.clone()
        previous = {p:transaction._decision_for(p) for p in range(self.config.players)}
        private = transaction._state.phase in ('passive','round_bonus')
        transaction._emit('action_applied', {'player':player,'action':{'id':selected.id,'kind':selected.kind,'params':dict(selected.params)}}, player if private else None)
        transaction._perform(player,selected)
        transaction._legal = {}
        transaction._sync_decisions(previous,player)
        transaction._receipt = transaction._finish_receipt()
        self.__dict__.update(transaction.__dict__)
        return self._detached(self._receipt['events'])

    def is_over(self):
        return self._state.phase == 'over'

    def result(self):
        if not self.is_over():
            return None
        scores = tuple(score_sheet(sheet) for sheet in self._state.sheets)
        resigned = self._state.resigned
        if resigned is None:
            return rank_scores(scores)
        # The others are ranked among themselves as at a normal end; the seat that surrendered places last.
        others = [seat for seat in range(len(scores)) if seat != resigned]
        ranked = rank_scores(tuple(scores[seat] for seat in others)) if others else None
        placements = [len(scores)] * len(scores)
        for seat, place in zip(others, ranked.placements if ranked else ()):
            placements[seat] = place
        full = rank_scores(scores)
        return replace(full, placements=tuple(placements), reason='resigned')

    def sheet_total(self, seat):
        """The seat's actual current score, including sheets frozen for other viewers."""
        return score_sheet(self._state.sheets[seat]).total

    def snapshot(self):
        if not self._recorded:
            raise ValueError('An unrecorded fork has no receipts to save')
        return persistence.snapshot(self)

    def clone(self):
        """An independent engine. Receipts are immutable once finished, so copies share them."""
        branch = type(self)()
        branch.config, branch._seed, branch._random = self.config, self._seed, self._random
        branch._future_seed, branch._forked = self._future_seed, self._forked
        branch._state, branch._sequence = self._state.copy(), self._sequence
        branch._revisions, branch._cursors = list(self._revisions), list(self._cursors)
        branch._decisions = list(self._decisions)
        branch._events, branch._audiences = [], []
        branch._receipt, branch._bootstrap = self._receipt, self._bootstrap
        branch._recorded = self._recorded
        return branch

    def fork(self, seed: int, *, recorded: bool = True):
        """Preserve all materialized state and select an independent future key.

        recorded=False is for imagined futures: same rules and views, but nothing to
        project or save, transitions are not atomic, and values are not detached.
        """
        future = KeyedRandom(seed)  # Validate before copying or touching state.
        branch = self.clone()
        branch._future_seed, branch._forked, branch._random = seed, True, future
        branch._recorded = recorded
        return branch
