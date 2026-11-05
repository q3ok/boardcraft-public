"""Semantic action construction; sheet legality remains in the entry helpers."""
from functools import lru_cache
import json
from boardcraft.contracts import Action
from .actions import EntryTarget, bonus_entries, legal_entries
from .randomness import DICE


def _new_action(kind: str, params: dict) -> Action:
    identity = kind + ':' + json.dumps(params, sort_keys=True, separators=(',', ':'))
    return Action(identity, kind, params)


# Shared values: the engine hands players detached copies, so nothing can edit these.
@lru_cache(maxsize=4096)
def action(kind: str, **params) -> Action:
    return _new_action(kind, params)


@lru_cache(maxsize=4096)
def entry_action(kind: str, entry: EntryTarget, die_id: str | None = None) -> Action:
    params = {'area': entry.area, 'target': list(entry.target) if isinstance(entry.target, tuple) else entry.target}
    if die_id is not None:
        params['die_id'] = die_id
    return _new_action(kind, params)


def die_actions(state, player: int, dice, kind='select_die') -> tuple[Action, ...]:
    return tuple(entry_action(kind, entry, die) for die in dice
                 for entry in legal_entries(state.sheets[player], die, state.dice))


def passive_actions(state, player: int) -> tuple[Action, ...]:
    return die_actions(state, player, state.tray) or die_actions(state, player, state.kept)


def window_actions(state, player: int) -> tuple[Action, ...]:
    window, sheet = state.windows[player], state.sheets[player]
    if not window.stack.is_empty:
        if window.stack.current is not None:
            return tuple(entry_action('place_bonus', entry) for entry in bonus_entries(sheet, window.stack.current.kind))
        return tuple(action('choose_bonus', bonus_id=choice.id) for choice in window.stack.available_choices())
    if window.stage == 'round':
        return tuple(action('choose_round_bonus', bonus=kind) for kind in ('blackX', 'black6'))
    if state.phase == 'regular' and player == state.active:
        choices = die_actions(state, player, state.available)
        if sheet.rerolls:
            return choices + (action('reroll'),) + (() if choices else (action('decline_reroll'),))
        return choices
    if window.stage == 'passive':
        return passive_actions(state, player)
    if window.stage == 'extra' and sheet.extras:
        choices = die_actions(state, player, [d for d in DICE if d not in window.extra_used], 'extra_die')
        if choices:
            return choices + (action('finish_extra'),)
    return ()


def resolve_entry(state, player: int, selected: Action) -> EntryTarget:
    params = selected.params
    if selected.kind == 'place_bonus':
        entries = bonus_entries(state.sheets[player], state.windows[player].stack.current.kind)
    else:
        entries = legal_entries(state.sheets[player], params['die_id'], state.dice)
    return next(entry for entry in entries if entry.area == params['area'] and
                (list(entry.target) if isinstance(entry.target, tuple) else entry.target) == params['target'])
