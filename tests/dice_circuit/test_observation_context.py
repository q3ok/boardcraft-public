"""Public schedule context and persistent mark-and-cross resource tracks."""
from copy import deepcopy

import pytest

from boardcraft.games.dice_circuit.actions import EntryTarget, apply_entry, award_rewards, reward_catalog
from boardcraft.games.dice_circuit.bonuses import BonusStack
from boardcraft.games.dice_circuit.state import PlayerSheet
from boardcraft.games.dice_circuit.turn_state import sheet_view
from boardcraft.games.dice_circuit.visible import visible_sheet
from boardcraft.registry import get_game
from engine_helpers import DICE, act, finish, new, regular, restore


def schedule(players, starting, turn_index, phase='regular'):
    engine = new(players)
    def fixture(s):
        active = (starting + turn_index) % players
        s.update(starting=starting, active=active, turn_index=turn_index, round_no=2,
                 phase=phase, available=list(DICE) if phase == 'regular' else [],
                 tray=[] if phase == 'regular' else list(DICE[:3] if players == 1 and phase == 'passive' else DICE),
                 kept=list(DICE[3:]) if players == 1 and phase == 'passive' else [],
                 regular_roll=0 if players == 1 and phase == 'passive' else 1)
        for seat, window in enumerate(s['windows']):
            window.update(stage=('regular' if seat == active else 'done') if phase == 'regular'
                          else ('extra' if seat == active else 'done') if phase == 'active_extra'
                          else ('passive' if players == 1 or seat != active else 'done'),
                          extra_used=[], stack={'version':1, 'frames':[], 'current':None})
        if phase == 'active_extra':
            s['sheets'][active].update(extras=1, extras_earned=1, extras_used=0)
        s['frozen_sheets'] = deepcopy(s['sheets']) if phase == 'passive' else None
    return restore(engine, fixture)


@pytest.mark.parametrize('players,total', [(1,6), (2,6), (3,5), (4,4)])
def test_initial_schedule_and_versions(players, total):
    engine = new(players)
    for viewer in range(players):
        obs = engine.observe(viewer)
        assert obs.observation_version == get_game('dice_circuit').observation_version == '1'
        assert obs.data['turn_index'] == 0
        assert obs.data['start_player'] == obs.data['active_player']
        assert (obs.data['rounds_total'], obs.data['rounds_remaining']) == (total, total-1)
        assert obs.data['active_turns_left'] == total
        assert obs.data['passive_turns_left'] == total * max(1, players-1)
        assert [sheet['active_done_this_round'] for sheet in obs.data['sheets']] == [False]*players
    observer = engine.observe(None).data
    assert 'active_turns_left' not in observer and 'passive_turns_left' not in observer


@pytest.mark.parametrize('players,start,index,expected', [
    (1,0,0, [(5,5,False,0)]),
    (2,1,1, [(5,4,False,0), (4,5,True,1)]),
    (3,2,1, [(4,7,False,0), (4,7,False,1), (3,8,True,2)]),
    (4,3,2, [(2,8,True,3), (3,7,False,0), (3,7,False,1), (2,8,True,2)]),
])
def test_hand_counted_midround_turns_and_clockwise_seats(players, start, index, expected):
    engine = schedule(players, start, index)
    for viewer, (active_left, passive_left, done, offset) in enumerate(expected):
        data = engine.observe(viewer).data
        assert (data['active_turns_left'], data['passive_turns_left']) == (active_left, passive_left)
        assert data['sheets'][viewer]['active_done_this_round'] is done
        assert data['sheets'][viewer]['seat_offset_from_active'] == offset
        assert (data['turn_index'], data['start_player']) == (index, start)


@pytest.mark.parametrize('players', [1,2,3,4])
def test_active_extra_still_counts_current_active_turn(players):
    engine = schedule(players, players-1, 0, 'active_extra')
    viewer = players-1
    data = engine.observe(viewer).data
    assert data['active_turns_left'] == {1:5,2:5,3:4,4:3}[players]
    assert data['sheets'][viewer]['active_done_this_round'] is False


@pytest.mark.parametrize('players,expected_active,expected_passive', [(1,4,5),(2,4,5),(3,3,8),(4,2,9)])
def test_current_passive_window_counts_and_active_turn_is_done(players, expected_active, expected_passive):
    engine = schedule(players, 0, 0, 'passive')
    viewer = 0 if players == 1 else 1
    data = engine.observe(viewer).data
    assert data['sheets'][0]['active_done_this_round'] is True
    assert (data['active_turns_left'], data['passive_turns_left']) == (
        expected_active if players == 1 else expected_active+1, expected_passive)


def test_finishing_other_passive_branch_never_changes_viewer_schedule_or_sheet():
    engine = schedule(3, 0, 0, 'passive')
    viewer = 2
    before = engine.observe(viewer)
    act(engine, player=1, kind='select_die')
    assert engine.observe(viewer) == before
    assert engine.observe(1).data['passive_turns_left'] == 7
    assert engine.observe(viewer).data['passive_turns_left'] == 8


def test_round_four_bonus_precedes_all_turns_and_over_has_no_remaining_turns():
    engine = new(3)
    def fixture(s):
        s.update(round_no=4, phase='round_bonus', available=[], tray=list(DICE), kept=[])
        for window in s['windows']:
            window.update(stage='round', extra_used=[], stack={'version':1,'frames':[],'current':None})
        s['frozen_sheets'] = deepcopy(s['sheets'])
    engine = restore(engine, fixture)
    for viewer in range(3):
        data = engine.observe(viewer).data
        assert (data['active_turns_left'], data['passive_turns_left']) == (2,4)
        assert not any(sheet['active_done_this_round'] for sheet in data['sheets'])
    finish(engine)
    for viewer in range(3):
        data = engine.observe(viewer).data
        assert (data['rounds_remaining'], data['active_turns_left'], data['passive_turns_left']) == (0,0,0)
        assert all(sheet['active_done_this_round'] for sheet in data['sheets'])


@pytest.mark.parametrize('resource,kind', [('rerolls','reroll'), ('extras','extra')])
def test_finite_printed_catalogue_bounds_track_even_when_rewards_arrive_together(resource, kind):
    sheet = PlayerSheet()
    rewards = tuple(r for r in reward_catalog() if r.kind == kind)
    assert len(rewards) == len({r.source_id for r in rewards}) == 7
    award_rewards(sheet, rewards)
    view = sheet_view(sheet)
    assert (view[resource+'_earned'], view[resource+'_used'], view[resource]) == (7,0,7)
    assert award_rewards(sheet, reversed(rewards)) == ()
    assert sheet_view(sheet) == view


def test_multiple_resources_from_one_bonus_chain_mark_two_distinct_slots():
    sheet = PlayerSheet(blue={2,3,5,7,8,10}, green=3)
    sheet.yellow.update({(0,0),(1,1),(2,2)})
    stack = BonusStack(apply_entry(sheet, EntryTarget('blue', 6, 6)))
    assert {c.id for c in stack.available_choices()} == {'blue:row:1', 'blue:column:1'}
    stack.select('blue:row:1')
    stack.place(sheet, EntryTarget('yellow', (3,3), None))
    assert sheet_view(sheet)['extras_earned'] == 1
    stack.select('blue:column:1')
    stack.place(sheet, EntryTarget('green', 3, None))
    assert stack.is_empty
    assert (sheet_view(sheet)['extras_earned'], sheet_view(sheet)['extras_used'], sheet.extras) == (2,0,2)


def test_spending_resources_preserves_earned_marks_and_survives_restore():
    engine = regular([2,2,5,4,6,3], available=('orange',), roll=3, rerolls=2)
    owner = engine.decision().player
    before = engine.observe(owner).data['sheets'][owner]
    act(engine, kind='reroll')
    after = engine.observe(owner).data['sheets'][owner]
    assert (before['rerolls_earned'], before['rerolls_used'], before['rerolls']) == (2,0,2)
    assert (after['rerolls_earned'], after['rerolls_used'], after['rerolls']) == (2,1,1)
    engine = restore(engine, lambda s:s['sheets'][owner].update(extras=1, extras_earned=1, extras_used=0))
    act(engine, kind='select_die', die_id='orange')
    act(engine, kind='extra_die', die_id='purple', area='purple')
    view = engine.observe(owner).data['sheets'][owner]
    assert (view['extras_earned'], view['extras_used'], view['extras']) == (1,1,0)
    assert visible_sheet(view).extras_used == 1
    assert engine.clone().snapshot() == engine.snapshot()
    assert engine.clone().observe(owner) == engine.observe(owner)


@pytest.mark.parametrize('changes', [
    {'rerolls_earned':8}, {'rerolls_used':True}, {'rerolls_used':-1},
    {'rerolls_earned':0}, {'rerolls_used':2}, {'rerolls':8, 'rerolls_earned':8},
    {'extras_earned':8}, {'extras_used':1}, {'extras_earned':1},
])
def test_restore_rejects_overflow_and_inconsistent_resource_tracks(changes):
    engine = new(2)
    with pytest.raises(ValueError):
        restore(engine, lambda s:s['sheets'][s['active']].update(changes))


def test_replaying_saved_decisions_preserves_all_resource_marks():
    engine = new(2, 31)
    saved = engine.snapshot()
    history = finish(engine)
    replay = get_game('dice_circuit').restore(saved)
    for player, action, decision_id, events in history:
        assert replay.apply(player, action, decision_id) == events
    assert replay.snapshot() == engine.snapshot()
    for sheet in replay.observe(None).data['sheets']:
        for resource in ('rerolls','extras'):
            assert 0 <= sheet[resource+'_used'] <= sheet[resource+'_earned'] <= 7
            assert sheet[resource] == sheet[resource+'_earned'] - sheet[resource+'_used']
