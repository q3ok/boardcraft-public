"""Depth-first sibling ordering, branch context and snapshot replay."""
from copy import deepcopy
import importlib.util
import json

import pytest

from boardcraft.contracts import IllegalAction
from boardcraft.games.dice_circuit.state import PlayerSheet


def api():
    assert importlib.util.find_spec('boardcraft.games.dice_circuit.bonuses') is not None, 'Bonus resolution is missing'
    from boardcraft.games.dice_circuit import actions, bonuses
    return actions, bonuses


def chain_fixture():
    sheet = PlayerSheet(blue={2, 4, 7, 11}, orange=[1, 2, 3], purple=[1, 2, 3, 4, 5],
                        earned_rewards={'orange:position:3', 'purple:position:3', 'purple:position:4', 'purple:position:5'},
                        rerolls=2, extras=1)
    sheet.yellow.update({(1, 0), (1, 1)})
    return sheet


def choose(stack, source):
    return stack.select(source)


@pytest.mark.parametrize('purple_first,added', [(True, 13), (False, 14)])
def test_order_sensitive_chain_finishes_descendants_before_sibling(purple_first, added):
    a, b = api()
    sheet = chain_fixture()
    rewards = a.apply_entry(sheet, a.EntryTarget('blue', 3, 3))
    assert [(r.source_id, r.kind) for r in rewards] == [('blue:row:0', 'orange5'), ('blue:column:2', 'purple6')]
    stack = b.BonusStack(rewards)
    assert {c.id for c in stack.available_choices()} == {'blue:row:0', 'blue:column:2'}
    if not purple_first:
        choose(stack, 'blue:row:0')
        stack.place(sheet, a.EntryTarget('orange', 3, 5))
    choose(stack, 'blue:column:2')
    assert stack.available_choices() == ()
    stack.place(sheet, a.EntryTarget('purple', 5, 6))
    choices = stack.available_choices()
    assert [(c.id, c.kind, c.ancestry) for c in choices] == [('purple:position:6', 'yellowX', ('blue:column:2',))]
    before = stack.snapshot()
    with pytest.raises(IllegalAction):
        choose(stack, 'blue:row:0')
    assert stack.snapshot() == before
    choose(stack, 'purple:position:6')
    stack.place(sheet, a.EntryTarget('yellow', (1, 3), None))
    choices = stack.available_choices()
    assert [(c.id, c.kind, c.ancestry) for c in choices] == [('yellow:row:1', 'orange4', ('blue:column:2', 'purple:position:6'))]
    choose(stack, 'yellow:row:1')
    stack.place(sheet, a.EntryTarget('orange', len(sheet.orange), 4))
    if not purple_first:
        choose(stack, 'orange:position:5')
        stack.place(sheet, a.EntryTarget('yellow', (0, 0), None))
    else:
        assert [c.id for c in stack.available_choices()] == ['blue:row:0']
        choose(stack, 'blue:row:0')
        stack.place(sheet, a.EntryTarget('orange', len(sheet.orange), 5))
        choose(stack, 'orange:position:5')
        stack.place(sheet, a.EntryTarget('yellow', (0, 0), None))
    assert sum(sheet.orange) - 6 == added
    assert sheet.orange[3:] == ([8, 5] if purple_first else [10, 4])
    assert stack.is_empty


def test_nested_siblings_only_selectable_at_own_depth_and_restore_midbranch():
    a, b = api()
    sheet = PlayerSheet(blue={2, 4, 7, 11})
    stack = b.BonusStack((a.Reward('round:4', 'blackX'), a.Reward('yellow:row:1', 'orange4')))
    choose(stack, 'round:4')
    stack.place(sheet, a.EntryTarget('blue', 3, None))
    assert {c.id for c in stack.available_choices()} == {'blue:row:0', 'blue:column:2'}
    assert all(c.ancestry == ('round:4',) for c in stack.available_choices())
    choose(stack, 'blue:column:2')
    restored = b.BonusStack.restore(json.loads(json.dumps(stack.snapshot())))
    clone_sheet = deepcopy(sheet)
    assert restored.current == stack.current
    assert restored.available_choices() == ()
    for branch, target_sheet in ((stack, sheet), (restored, clone_sheet)):
        with pytest.raises(IllegalAction):
            choose(branch, 'yellow:row:1')
        branch.place(target_sheet, a.EntryTarget('purple', 0, 6))
        assert [c.id for c in branch.available_choices()] == ['blue:row:0']
        choose(branch, 'blue:row:0')
        branch.place(target_sheet, a.EntryTarget('orange', 0, 5))
        assert [c.id for c in branch.available_choices()] == ['yellow:row:1']
    assert restored.snapshot() == stack.snapshot()
    assert clone_sheet == sheet


def test_full_bonus_expires_without_entry_or_reward_and_resources_are_leaves():
    a, b = api()
    sheet = PlayerSheet(green=11)
    stack = b.BonusStack((a.Reward('blue:column:1', 'greenX'), a.Reward('green:position:4', 'extra'), a.Reward('green:position:7', 'endgame')))
    assert [c.id for c in stack.available_choices()] == ['blue:column:1']
    choose(stack, 'blue:column:1')
    before = deepcopy(sheet)
    stack.expire_current(sheet)
    assert sheet == before
    assert stack.is_empty


def test_invalid_bonus_placement_and_expiry_preserve_sheet_and_stack():
    a, b = api()
    sheet = PlayerSheet()
    stack = b.BonusStack((a.Reward('blue:row:0', 'orange5'),))
    choose(stack, 'blue:row:0')
    before_sheet, before_stack = deepcopy(sheet), stack.snapshot()
    for operation in (lambda: stack.place(sheet, a.EntryTarget('orange', 0, 4)), lambda: stack.expire_current(sheet), lambda: stack.select('blue:row:0')):
        with pytest.raises(IllegalAction):
            operation()
        assert sheet == before_sheet
        assert stack.snapshot() == before_stack


def test_external_chain_cannot_interleave_and_complete_requires_selection():
    a, b = api()
    stack = b.BonusStack((a.Reward('blue:row:0', 'orange5'),))
    before = stack.snapshot()
    with pytest.raises(IllegalAction):
        stack.start((a.Reward('purple:position:6', 'yellowX'),))
    with pytest.raises(IllegalAction):
        stack.complete_current()
    assert stack.snapshot() == before


def test_resource_earned_in_chain_is_immediate_without_resource_choice():
    a, b = api()
    sheet = PlayerSheet(orange=[1, 2, 3, 8, 5], extras=8,
                        earned_rewards={'orange:position:3', 'orange:position:5'})
    stack = b.BonusStack((a.Reward('purple:position:10', 'orange6'),))
    choose(stack, 'purple:position:10')
    rewards = stack.place(sheet, a.EntryTarget('orange', 5, 6))
    assert [(r.source_id, r.kind) for r in rewards] == [('orange:position:6', 'extra')]
    assert sheet.extras == 9
    assert stack.available_choices() == ()
    assert stack.is_empty


def test_duplicate_branch_source_rejected_without_losing_current_branch():
    a, b = api()
    stack = b.BonusStack((a.Reward('blue:row:0', 'orange5'), a.Reward('blue:column:2', 'purple6')))
    choose(stack, 'blue:column:2')
    before = stack.snapshot()
    for duplicate in (a.Reward('blue:row:0', 'orange5'), a.Reward('blue:column:2', 'purple6')):
        with pytest.raises(IllegalAction):
            stack.complete_current((duplicate,))
        assert stack.snapshot() == before


def test_unknown_reward_identity_cannot_enter_persistent_stack():
    a, b = api()
    with pytest.raises(IllegalAction):
        b.BonusStack((a.Reward('random-identity', 'yellowX'),))

@pytest.mark.parametrize('snapshot', [
    {'version':1,'frames':[{'pending':[],'ancestry':[]}],'current':None},
    {'version':1,'frames':[],'current':{'id':'blue:column:2','kind':'purple6','ancestry':[]}},
    {'version':1,'frames':[{'pending':[{'source_id':'purple:position:6','kind':'yellowX'}],'ancestry':['blue:column:2']}],'current':None},
    {'version':1,'frames':[{'pending':[],'ancestry':[]}],'current':{'id':'blue:column:2','kind':'purple6','ancestry':['round:4']}},
])
def test_bonus_restore_rejects_unresumable_frame_shapes(snapshot):
    _, b = api()
    with pytest.raises(ValueError):
        b.BonusStack.restore(snapshot)


def test_bonus_restore_preserves_empty_ancestors_and_selected_empty_top_frame():
    a, b = api()
    stack = b.BonusStack((a.Reward('blue:column:2','purple6'),))
    stack.select('blue:column:2')
    assert b.BonusStack.restore(stack.snapshot()).snapshot() == stack.snapshot()
    sheet = PlayerSheet(purple=[1,2,3,4,5])
    stack.place(sheet,a.EntryTarget('purple',5,6))
    assert stack.snapshot()['frames'][0]['pending'] == []
    assert b.BonusStack.restore(stack.snapshot()).snapshot() == stack.snapshot()
    stack.select('purple:position:6')
    assert stack.snapshot()['frames'][-1]['pending'] == []
    assert b.BonusStack.restore(stack.snapshot()).snapshot() == stack.snapshot()
