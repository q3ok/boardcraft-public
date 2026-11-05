"""Entry legality, atomic rejection and printed reward behavior."""
from copy import deepcopy

import pytest

from boardcraft.contracts import IllegalAction
from boardcraft.games.dice_circuit import actions
from boardcraft.games.dice_circuit.state import PlayerSheet


def api():
    assert hasattr(actions, 'legal_entries'), 'Entry rules are missing'
    return actions


def test_yellow_duplicate_choice_and_white_five_areas():
    a = api()
    sheet = PlayerSheet()
    assert {entry.target for entry in a.legal_entries(sheet, 'yellow', {'yellow': 2})} == {(1, 0), (2, 2)}
    choices = a.legal_entries(sheet, 'white', {'white': 4, 'blue': 2})
    assert {entry.area for entry in choices} == {'yellow', 'blue', 'green', 'orange', 'purple'}
    a.apply_entry(sheet, next(entry for entry in choices if entry.area == 'yellow' and entry.target == (2, 3)))
    assert (2, 3) in sheet.yellow
    assert (3, 2) not in sheet.yellow


@pytest.mark.parametrize('die_id', ['blue', 'white'])
def test_blue_uses_both_values_without_zone_dependency(die_id):
    a = api()
    entry = next(entry for entry in a.legal_entries(PlayerSheet(), die_id, {'blue': 2, 'white': 4}) if entry.area == 'blue')
    assert (entry.target, entry.value) == (6, 6)
    sheet = PlayerSheet()
    a.apply_entry(sheet, entry)
    assert sheet.blue == {6}


def test_green_next_only_and_cross_ignores_threshold():
    a = api()
    sheet = PlayerSheet(green=4, earned_rewards={'green:position:4'})
    assert a.legal_entries(sheet, 'green', {'green': 4}) == ()
    assert a.legal_entries(sheet, 'green', {'green': 5}) == (a.EntryTarget('green', 4, 5),)
    assert a.bonus_entries(sheet, 'greenX') == (a.EntryTarget('green', 4, None),)
    a.apply_entry(sheet, a.bonus_entries(sheet, 'greenX')[0])
    assert sheet.green == 5


def test_orange_multiplier_applied_once_and_no_retroactive_rewards():
    a = api()
    sheet = PlayerSheet(orange=[1, 2, 3], earned_rewards={'orange:position:3'}, rerolls=1)
    assert a.apply_entry(sheet, a.EntryTarget('orange', 3, 4)) == ()
    assert sheet.orange == [1, 2, 3, 8]
    assert sheet.rerolls == 1


@pytest.mark.parametrize('previous,value,legal', [(5, 3, False), (5, 6, True), (6, 6, True), (6, 1, True)])
def test_purple_increase_and_six_reset(previous, value, legal):
    a = api()
    choices = a.legal_entries(PlayerSheet(purple=[previous]), 'purple', {'purple': value})
    assert bool(choices) is legal
    if legal:
        assert choices == (a.EntryTarget('purple', 1, value),)


@pytest.mark.parametrize('area,target,value', [
    ('yellow', (0, 3), None), ('yellow', (0, 0), 2), ('yellow', (4, 0), None),
    ('blue', 6, 6), ('blue', 1, None), ('blue', 3, 4),
    ('green', 0, 6), ('green', 1, 1), ('orange', 1, 3), ('purple', 0, 6),
    ('orange', 0, 0), ('orange', 0, 7), ('orange', 0, True), ('purple', 1, 3),
    ('orange', 0, None), ('purple', 1, None), ('green', True, 6), ('unknown', 0, 1),
])
def test_invalid_or_occupied_entry_is_transactional(area, target, value):
    a = api()
    sheet = PlayerSheet(blue={6}, green=1, purple=[5])
    before = deepcopy(sheet)
    with pytest.raises(IllegalAction):
        a.apply_entry(sheet, a.EntryTarget(area, target, value))
    assert sheet == before


@pytest.mark.parametrize('die_id,values', [('yellow', {'yellow': True}), ('white', {'white': 0, 'blue': 2}), ('blue', {'blue': 7, 'white': 2}), ('blue', {'blue': 2}), ('black', {'black': 3})])
def test_invalid_dice_values_rejected_without_mutation(die_id, values):
    a = api()
    sheet = PlayerSheet()
    with pytest.raises(IllegalAction):
        a.legal_entries(sheet, die_id, values)
    assert sheet == PlayerSheet()


def test_cross_and_black_bonus_targets_and_full_area_expiry():
    a = api()
    sheet = PlayerSheet()
    assert a.EntryTarget('yellow', (0, 0), None) in a.bonus_entries(sheet, 'yellowX')
    assert {entry.area for entry in a.bonus_entries(sheet, 'blackX')} == {'yellow', 'blue', 'green'}
    assert {entry.area for entry in a.bonus_entries(sheet, 'black6')} == {'orange', 'purple'}
    sheet.yellow.update((row, column) for row in range(4) for column in range(4))
    sheet.blue.update(range(2, 13))
    sheet.green = 11
    sheet.orange = [1] * 11
    sheet.purple = [6] * 11
    for kind in ('yellowX', 'blueX', 'greenX', 'orange4', 'purple6', 'blackX', 'black6'):
        assert a.bonus_entries(sheet, kind) == ()


def test_reward_only_for_newly_completed_source_and_no_resource_cap():
    a = api()
    sheet = PlayerSheet(green=3, rerolls=9, extras=9, earned_rewards=set())
    rewards = a.apply_entry(sheet, a.EntryTarget('green', 3, 4))
    assert [(reward.source_id, reward.kind) for reward in rewards] == [('green:position:4', 'extra')]
    assert sheet.extras == 10
    assert a.apply_entry(sheet, a.EntryTarget('green', 4, 5)) == ()
    assert sheet.extras == 10
    assert sheet.rerolls == 9
    sheet.green = 6
    sheet.earned_rewards.add('green:position:6')
    rewards = a.apply_entry(sheet, a.EntryTarget('green', 6, None))
    assert [(reward.source_id, reward.kind) for reward in rewards] == [('green:position:7', 'endgame')]
    assert sheet.emblems == 1


def test_completed_yellow_row_and_diagonal_awarded_once():
    a = api()
    sheet = PlayerSheet()
    for target in ((0, 0), (0, 1)):
        assert a.apply_entry(sheet, a.EntryTarget('yellow', target, None)) == ()
    rewards = a.apply_entry(sheet, a.EntryTarget('yellow', (0, 2), None))
    assert [(r.source_id, r.kind) for r in rewards] == [('yellow:row:0', 'blueX')]
    for target in ((1, 1), (2, 2)):
        a.apply_entry(sheet, a.EntryTarget('yellow', target, None))
    rewards = a.apply_entry(sheet, a.EntryTarget('yellow', (3, 3), None))
    assert [(r.source_id, r.kind) for r in rewards] == [('yellow:diagonal', 'extra')]
    assert sheet.extras == 1


def test_previously_earned_source_cannot_grant_reward_again():
    a = api()
    sheet = PlayerSheet(green=3, extras=1, earned_rewards={'green:position:4'})
    assert a.apply_entry(sheet, a.EntryTarget('green', 3, None)) == ()
    assert sheet.extras == 1


def test_round_rewards_share_once_only_awards_and_validate_before_mutation():
    a = api()
    assert hasattr(a, 'award_rewards'), 'Once-only external reward grants are missing'
    sheet = PlayerSheet(rerolls=8)
    reward = a.Reward('round:1', 'reroll')
    assert a.award_rewards(sheet, (reward,)) == (reward,)
    assert a.award_rewards(sheet, (reward,)) == ()
    assert sheet.rerolls == 9
    choice = a.Reward('round:4', 'blackX')
    assert a.award_rewards(sheet, (choice,)) == (choice,)
    assert a.award_rewards(sheet, (a.Reward('round:4', 'black6'),)) == ()
    before = deepcopy(sheet)
    with pytest.raises(IllegalAction):
        a.award_rewards(sheet, (a.Reward('round:2', 'extra'), a.Reward('invented', 'extra')))
    assert sheet == before
