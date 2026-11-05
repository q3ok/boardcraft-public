"""Deterministic automatic advancement, independent of transport/view cursors."""
from .actions import Reward, apply_entry, award_rewards
from .randomness import DICE
from .sheet import load_sheet
from .turn_actions import die_actions, passive_actions, resolve_entry, window_actions
from .turn_state import PlayerWindow, sheet_view


class TurnFlow:
    """Engine-owned flow mixin; emit is supplied by the engine receipt boundary."""

    def _roll(self, phase='active'):
        s = self._state
        for die in s.available:
            s.dice[die] = self._random.die(s.round_no, s.active, phase, s.regular_roll, s.reroll_no, die)
        self._emit('dice_rolled', {'phase': phase, 'opportunity': s.regular_roll,
                                 'values': {die: s.dice[die] for die in s.available}})

    def _start_round(self):
        s = self._state
        s.active = (s.starting + s.turn_index) % self.config.players
        self._emit('round_started', {'round': s.round_no})
        reward = load_sheet().round_rewards[s.round_no - 1]
        if reward == 'blackX-or-black6':
            s.phase = 'round_bonus'
            s.windows = [PlayerWindow('round') for _ in s.sheets]
            s.frozen_sheets = [sheet_view(sheet) for sheet in s.sheets]
        else:
            if reward is not None:
                for sheet in s.sheets:
                    award_rewards(sheet, (Reward(f'round:{s.round_no}', reward),))
                self._emit('round_reward', {'round': s.round_no, 'reward': reward})
            self._start_active()

    def _start_active(self):
        s = self._state
        s.active = (s.starting + s.turn_index) % self.config.players
        s.phase, s.frozen_sheets = 'regular', None
        s.windows = [PlayerWindow() for _ in s.sheets]
        s.windows[s.active].stage = 'regular'
        s.available, s.tray, s.kept = list(DICE), [], []
        s.regular_roll, s.reroll_no = 1, 0
        self._emit('turn_started', {'player': s.active, 'round': s.round_no, 'simulated': False})
        self._roll()

    def _after_opportunity(self):
        s = self._state
        if not s.available or s.regular_roll == 3:
            s.tray.extend(s.available)
            s.available = []
            s.phase = 'active_extra'
            s.windows[s.active].stage = 'extra'
        else:
            s.regular_roll += 1
            s.reroll_no = 0
            self._roll()

    def _start_passive(self):
        s = self._state
        s.phase = 'passive'
        s.windows = [PlayerWindow('passive' if p != s.active else 'done') for p in range(self.config.players)]
        if self.config.players == 1:
            s.windows[0].stage = 'passive'
            s.available, s.tray, s.kept = list(DICE), [], []
            s.regular_roll, s.reroll_no = 0, 0
            self._emit('turn_started', {'player': 0, 'round': s.round_no, 'simulated': True})
            self._roll('solo_passive')
            ordered = sorted(DICE, key=lambda die: (s.dice[die], self._random.proximity(s.round_no, 0, die)))
            s.tray, s.kept, s.available = ordered[:3], ordered[3:], []
            self._emit('tray_formed', {'tray': list(s.tray)})
        s.frozen_sheets = [sheet_view(sheet) for sheet in s.sheets]
        self._emit('passive_started', {'active_player': s.active})

    def _next_turn(self):
        s = self._state
        s.frozen_sheets = None
        s.turn_index += 1
        if s.turn_index == self.config.players:
            s.turn_index = 0
            if s.round_no == load_sheet().round_counts[self.config.players - 1]:
                s.phase = 'over'
                self._emit('game_finished', {'scores': list(self.result().scores)})
                return
            s.round_no += 1
            self._start_round()
        else:
            self._start_active()

    def _advance(self):
        """Stop only on a genuine choice; every transition has a bounded measure."""
        s = self._state
        while s.phase != 'over':
            changed = False
            for player in self._eligible_players():
                window, sheet = s.windows[player], s.sheets[player]
                while not window.stack.is_empty:
                    choices = window_actions(s, player)
                    if window.stack.current is not None and not choices:
                        window.stack.expire_current(sheet)
                    elif len(choices) == 1:
                        selected = choices[0]
                        bonus_id = window.stack.current.id if window.stack.current is not None else selected.params['bonus_id']
                        private = s.phase in ('passive', 'round_bonus')
                        self._emit('bonus_auto_resolved', {
                            'player': player, 'bonus_id': bonus_id,
                            'action': {'id': selected.id, 'kind': selected.kind, 'params': dict(selected.params)},
                        }, player if private else None)
                        self._perform_bonus(player, selected)
                    else:
                        break
                    changed = True
                self._resume_regular()
                if not window.stack.is_empty:
                    continue
                if s.phase == 'regular':
                    if not die_actions(s, player, s.available) and not sheet.rerolls:
                        self._emit('opportunity_unused', {'player': player, 'opportunity': s.regular_roll})
                        self._after_opportunity()
                        changed = True
                        break
                elif window.stage == 'passive' and not passive_actions(s, player):
                    window.stage = 'extra'
                    changed = True
                if window.stage == 'extra' and not window_actions(s, player):
                    window.stage = 'done'
                    changed = True
            if s.phase == 'regular':
                if not changed:
                    return
            elif s.phase == 'active_extra':
                if s.windows[s.active].stage == 'done':
                    self._start_passive()
                    changed = True
            elif s.phase in ('passive', 'round_bonus'):
                if all(w.stage == 'done' and w.stack.is_empty for w in s.windows):
                    previous_phase = s.phase
                    self._emit('window_published', {'phase': previous_phase, 'sheets': [sheet_view(sheet) for sheet in s.sheets]})
                    if previous_phase == 'passive':
                        self._next_turn()
                    else:
                        self._start_active()
                    changed = True
            if not changed:
                return

    def _perform(self, player, selected):
        s, window = self._state, self._state.windows[player]
        kind = selected.kind
        if kind == 'reroll':
            s.sheets[player].rerolls -= 1
            s.sheets[player].rerolls_used += 1
            s.reroll_no += 1
            self._roll()
        elif kind == 'decline_reroll':
            self._after_opportunity()
        elif kind == 'finish_extra':
            window.stage = 'done'
        elif kind == 'choose_round_bonus':
            rewards = award_rewards(s.sheets[player], (Reward(f'round:{s.round_no}', selected.params['bonus']),))
            window.stage = 'done'
            window.stack.start(rewards)
        elif kind in ('choose_bonus', 'place_bonus'):
            self._perform_bonus(player, selected)
        else:
            entry = resolve_entry(s, player, selected)
            rewards = apply_entry(s.sheets[player], entry)
            window.stack.start(rewards)
            die = selected.params['die_id']
            if kind == 'extra_die':
                s.sheets[player].extras -= 1
                s.sheets[player].extras_used += 1
                window.extra_used.add(die)
            elif s.phase == 'regular':
                s.available.remove(die)
                s.kept.append(die)
                lower = [d for d in s.available if s.dice[d] < s.dice[die]]
                s.tray.extend(lower)
                s.available = [d for d in s.available if d not in lower]
                window.stage = 'after_regular'
            else:
                window.stage = 'extra'
        self._resume_regular()
        self._advance()

    def _perform_bonus(self, player, selected):
        s, stack = self._state, self._state.windows[player].stack
        if selected.kind == 'choose_bonus':
            stack.select(selected.params['bonus_id'])
        else:
            stack.place(s.sheets[player], resolve_entry(s, player, selected))

    def _resume_regular(self):
        s = self._state
        window = s.windows[s.active]
        if s.phase == 'regular' and window.stage == 'after_regular' and window.stack.is_empty:
            window.stage = 'regular'
            self._after_opportunity()
