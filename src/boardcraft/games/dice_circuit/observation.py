"""Game-authoritative perspective views with simultaneous-window barriers."""
from boardcraft.contracts import Observation
from .randomness import DICE
from .turn_state import bonus_view, sheet_view
from .scoring import result_view, score_sheet, score_view
from .visible import visible_sheet
from .sheet import load_sheet


def active_done(state, seat, players):
    """Active turns finish before the simultaneous passive window begins."""
    position = (seat - state.starting) % players
    return state.phase == 'over' or position < state.turn_index or (
        position == state.turn_index and state.phase == 'passive')


def turns_left(state, viewer, players, future_rounds):
    """Count unfinished turns using only the viewer's private window progress."""
    if state.phase == 'over':
        return 0, 0
    active = future_rounds + int(not active_done(state, viewer, players))
    window = state.windows[viewer]
    own_passive_done = state.phase == 'passive' and window.stage == 'done' and window.stack.is_empty
    if players == 1:
        passive = future_rounds + int(not own_passive_done)
    else:
        passive = future_rounds * (players - 1) + sum(
            seat != viewer and (seat - state.starting) % players >= state.turn_index
            for seat in range(players))
        if viewer != state.active and own_passive_done:
            passive -= 1
    return active, passive


def observe(engine, viewer):
    engine._validate_viewer(viewer)
    s = engine._state
    sheets = [sheet_view(sheet) for sheet in s.sheets]
    if s.phase in ('passive','round_bonus') and s.frozen_sheets is not None:
        sheets = [dict(sheet) for sheet in s.frozen_sheets]  # Observation detaches the nested values.
        if viewer is not None and (s.phase == 'round_bonus' or viewer != s.active or engine.config.players == 1):
            sheets[viewer] = sheet_view(s.sheets[viewer])
    bonus_owner = s.active if s.phase in ('regular', 'active_extra') else viewer
    resolving_bonus = bonus_owner is not None and not s.windows[bonus_owner].stack.is_empty
    players = engine.config.players
    total = load_sheet().round_counts[players - 1]
    remaining = 0 if s.phase == 'over' else total - s.round_no
    for seat, sheet in enumerate(sheets):
        sheet['active_done_this_round'] = active_done(s, seat, players)
        sheet['seat_offset_from_active'] = (seat - s.active) % players
    data = {'variant':engine.config.variant, 'players':engine.config.players,
            'round':s.round_no, 'active_player':s.active, 'phase':'bonus' if resolving_bonus else s.phase,
            'turn_phase':s.phase,
            'turn_index':s.turn_index, 'start_player':s.starting,
            'rounds_total':total, 'rounds_remaining':remaining,
            'regular_opportunity':s.regular_roll,
            'dice':{die:{'value':s.dice[die], 'zone':'available' if die in s.available else 'kept' if die in s.kept else 'tray'} for die in DICE},
            'sheets':sheets, 'event_cursor':engine._cursors[engine._viewer_index(viewer)]}
    scores = [score_sheet(visible_sheet(sheet)) for sheet in sheets]
    data['score_summary'] = [score_view(score) for score in scores]
    if viewer is not None:
        window = s.windows[viewer]
        data['active_turns_left'], data['passive_turns_left'] = turns_left(s, viewer, players, remaining)
        data['window'] = {'stage':window.stage, 'extra_used':sorted(window.extra_used), 'bonus_stack':bonus_view(window.stack)}
    if s.resigned is not None:
        data['resigned'] = s.resigned
    result = engine.result()
    if result is not None:
        data['result'] = result_view(result)
    build = Observation if engine._recorded else Observation.owned
    return build('dice_circuit','1','1',viewer,engine._revisions[engine._viewer_index(viewer)],engine._decision_for(viewer),data)
