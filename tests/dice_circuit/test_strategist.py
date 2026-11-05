"""Strategist searches canonical bonus chains using only its owner's view."""
from copy import deepcopy
from dataclasses import replace

import pytest

from engine_helpers import new
from boardcraft.games.dice_circuit.actions import EntryTarget, Reward, apply_entry
from boardcraft.games.dice_circuit.bonuses import BonusStack
from boardcraft.games.dice_circuit.scoring import score_sheet
from boardcraft.games.dice_circuit.state import PlayerSheet
from boardcraft.games.dice_circuit.turn_actions import action
from boardcraft.games.dice_circuit.turn_state import bonus_view, sheet_view


def bot(seed=42):
    from boardcraft.games.dice_circuit.bots import StrategistBot
    return StrategistBot(seed)


@pytest.mark.parametrize('players', [1, 2, 3, 4])
def test_legal_deterministic_complete_games_and_no_input_mutation(players):
    engine = new(players, 19)
    first, second = bot(), bot()
    count = 0
    while not engine.is_over():
        decision = engine.decision()
        obs, legal = engine.observe(decision.player), engine.legal_actions(decision.player)
        saved = deepcopy(obs)
        choice = first.choose(obs, legal)
        assert choice == second.choose(obs, legal)
        assert choice.action in legal and choice.explanation.isascii()
        assert obs == saved
        engine.apply(decision.player, choice.action, decision.id)
        count += 1
        assert count < 1500
    assert len(engine.result().scores) == players
    with pytest.raises(ValueError, match='legal'):
        first.choose(engine.observe(0), ())


def test_final_extra_closes_emblem_and_counts_score_once():
    obs = new(1).observe(0)
    data = deepcopy(obs.data)
    sheet = PlayerSheet(green=6, blue={2, 3, 4, 5, 6, 7}, orange=[5]*6,
                        purple=[1, 6, 2, 6, 3], extras=1, emblems=3)
    sheet.yellow.update((r, 0) for r in range(4))
    data.update(round=6, rounds_remaining=0, active_turns_left=0, passive_turns_left=1,
                phase='passive', turn_phase='passive')
    data['window'].update(stage='extra', extra_used=[])
    data['sheets'][0] = sheet_view(sheet)
    data['dice']['green']['value'] = 2
    data['dice']['orange']['value'] = 1
    obs = replace(obs, data=data)
    emblem = action('extra_die', die_id='green', area='green', target=6)
    weak = action('extra_die', die_id='orange', area='orange', target=6)
    policy = bot()
    assert policy.choose(obs, (weak, emblem, action('finish_extra'))).action == emblem
    from boardcraft.games.dice_circuit.strategist import Evaluation
    evaluation = Evaluation(obs, policy)
    before = score_sheet(sheet).total
    after = deepcopy(sheet)
    apply_entry(after, EntryTarget('green', 6, 2))
    assert evaluation.move_value(emblem) == score_sheet(after).total - before
    assert evaluation.state_value(after) == score_sheet(after).total


def test_bonus_search_resolves_children_and_locked_siblings():
    from boardcraft.games.dice_circuit.strategist import Evaluation
    obs = new(1).observe(0)
    sheet = PlayerSheet(blue={3, 7}, purple=[1, 2, 6])
    stack = BonusStack((Reward('yellow:row:0', 'blueX'),))
    stack.select('yellow:row:0')
    data = deepcopy(obs.data)
    data['sheets'][0] = sheet_view(sheet)
    data['window']['bonus_stack'] = bonus_view(stack)
    evaluation = Evaluation(replace(obs, data=data), bot())
    chosen = action('place_bonus', area='blue', target=11)
    after = deepcopy(sheet)
    rewards = apply_entry(after, EntryTarget('blue', 11, None))
    assert any(r.kind == 'purple6' for r in rewards)
    value = evaluation.move_value(chosen)
    # Full chain includes the purple 6, its blue cross, and any descendants.
    assert value > evaluation.state_value(after) - evaluation.state_value(sheet)


def test_other_players_sheets_and_event_cursor_are_irrelevant():
    engine = new(2, 3)
    decision = engine.decision()
    obs, legal = engine.observe(decision.player), engine.legal_actions(decision.player)
    data = deepcopy(obs.data)
    data['sheets'][1 - obs.viewer] = {'hidden': 'never inspect another sheet'}
    data['score_summary'] = []
    data['event_cursor'] = 987654
    assert bot().choose(obs, legal) == bot().choose(replace(obs, data=data), legal)


def test_bonus_sibling_order_uses_the_stronger_number_on_the_multiplier():
    from boardcraft.games.dice_circuit.strategist import Evaluation
    from boardcraft.games.dice_circuit.sheet import load_sheet
    spec = load_sheet()
    obs = new(1).observe(0)
    sheet = PlayerSheet(blue=set(spec.blue.values), green=11, orange=[1]*3, purple=[6]*11)
    sheet.yellow.update((r, c) for r in range(4) for c in range(4))
    rewards = (Reward('yellow:row:1', 'orange4'), Reward('blue:row:0', 'orange5'))
    sheet.earned_rewards.update(r.source_id for r in rewards)
    stack = BonusStack(rewards)
    data = deepcopy(obs.data)
    data.update(round=6, rounds_remaining=0, active_turns_left=0, passive_turns_left=1,
                phase='bonus', turn_phase='passive')
    data['window'].update(stage='extra', bonus_stack=bonus_view(stack))
    data['sheets'][0] = sheet_view(sheet)
    obs = replace(obs, data=data)
    four = action('choose_bonus', bonus_id='yellow:row:1')
    five = action('choose_bonus', bonus_id='blue:row:0')
    policy = bot()
    evaluation = Evaluation(obs, policy)
    assert evaluation.move_value(five) == 14
    assert evaluation.move_value(four) == 13
    assert policy.choose(obs, (four, five)).action == five


def test_physical_blue_face_controls_future_dice_loss():
    from boardcraft.games.dice_circuit.strategist import Evaluation
    obs = new(1).observe(0)
    data = deepcopy(obs.data)
    for item in data['dice'].values():
        item.update(value=4, zone='available')
    data['dice']['blue']['value'] = 1
    data['dice']['white']['value'] = 6
    evaluation = Evaluation(replace(obs, data=data), bot())
    low = action('select_die', die_id='blue', area='blue', target=7)
    high = action('select_die', die_id='white', area='blue', target=7)
    assert evaluation.dice_loss(low) == 0
    assert evaluation.dice_loss(high) > 4
    data['regular_opportunity'] = 3
    assert Evaluation(replace(obs, data=data), bot()).dice_loss(high) == 0


def test_final_passive_regular_choice_has_no_future_chain_tiebreak():
    from boardcraft.games.dice_circuit.strategist import Evaluation
    obs = new(1).observe(0)
    data = deepcopy(obs.data)
    data.update(round=6, rounds_remaining=0, active_turns_left=0, passive_turns_left=1,
                phase='passive', turn_phase='passive')
    data['window']['stage'] = 'passive'
    evaluation = Evaluation(replace(obs, data=data), bot())
    assert evaluation.final
    assert evaluation.time == 0


@pytest.mark.parametrize('phase,stage,roll', [('active_extra', 'extra', 3), ('regular', 'regular', 3)])
def test_last_multiplayer_active_window_has_no_future_resource_value(phase, stage, roll):
    from boardcraft.games.dice_circuit.strategist import Evaluation
    from boardcraft.games.dice_circuit.sheet import load_sheet
    obs = new(2).observe(0)
    spec = load_sheet()
    sheet = PlayerSheet(blue=set(spec.blue.values), green=11, orange=[6]*9, purple=[6]*11, extras=1)
    sheet.yellow.update((r, c) for r in range(4) for c in range(4))
    data = deepcopy(obs.data)
    data.update(round=6, rounds_remaining=0, active_player=0, active_turns_left=1, passive_turns_left=0,
                phase=phase, turn_phase=phase, regular_opportunity=roll)
    data['window']['stage'] = stage
    data['sheets'][0] = sheet_view(sheet)
    for item in data['dice'].values():
        item['value'] = 1
    obs = replace(obs, data=data)
    policy = bot()
    evaluation = Evaluation(obs, policy)
    assert evaluation.final
    assert evaluation.time == 0
    assert evaluation.extra_reserve() == 0
    if stage == 'extra':
        positive = action('extra_die', die_id='orange', area='orange', target=9)
        assert evaluation.move_value(positive) == 1
        assert policy.choose(obs, (positive, action('finish_extra'))).action == positive


def test_rerolls_have_no_value_after_the_last_own_active_turn():
    from boardcraft.games.dice_circuit.strategist import Evaluation
    obs = new(4).observe(0)
    data = deepcopy(obs.data)
    data.update(active_turns_left=0, passive_turns_left=2, phase='passive', turn_phase='passive')
    data['window']['stage'] = 'passive'
    evaluation = Evaluation(replace(obs, data=data), bot())
    assert not evaluation.final
    sheet = evaluation.sheet
    after = deepcopy(sheet)
    after.rerolls += 1
    assert evaluation.reward_value('reroll', sheet, 0) == 0
    assert evaluation.state_value(after) == evaluation.state_value(sheet)


def test_strategist_catalog_and_service_exposure(tmp_path):
    from boardcraft.inference.catalog import RuntimeBotCatalog
    from boardcraft.registry import get_game
    module = get_game('dice_circuit')
    assert 'strategist' in module.info().bot_kinds
    catalog = RuntimeBotCatalog()
    descriptor = next(d for d in catalog.list_bots('dice_circuit') if d.choice_id == 'strategist')
    assert descriptor.display_name == 'Strategist'
    from boardcraft.server.service import PlayService
    exposed = next(d for d in PlayService(tmp_path).list_bots('dice_circuit') if d['choice_id'] == 'strategist')
    assert exposed['description'] and exposed['description'].isascii()
    assert catalog.resolve(descriptor.reference, 2) is not None
