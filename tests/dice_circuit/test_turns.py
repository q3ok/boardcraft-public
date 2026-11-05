"""Physical dice movement, opportunities, resources and bonus integration."""
import pytest
from boardcraft.registry import get_game
from boardcraft.contracts import GameConfig
from engine_helpers import DICE, act, new, regular, restore


def test_strict_lower_cutoff_uses_physical_white_not_blue_sum(monkeypatch):
    engine = regular([2, 1, 3, 4, 5, 2])
    active = engine.decision().player
    from boardcraft.games.dice_circuit.randomness import KeyedRandom
    def die(self, round_no, active_seat, phase, regular_roll, reroll_no, die_id):
        return dict(zip(DICE, [2, 1, 3, 4, 6, 4]))[die_id]
    monkeypatch.setattr(KeyedRandom, 'die', die)
    act(engine, kind='select_die', die_id='yellow', area='yellow', target=[1, 0])
    s = engine.snapshot()['state']
    assert s['tray'] == ['blue']
    assert s['kept'] == ['yellow']
    assert s['available'] == ['green', 'orange', 'purple', 'white']
    act(engine, kind='select_die', die_id='white', area='blue', target=5)
    s = engine.snapshot()['state']
    assert s['tray'] == ['blue', 'green']
    assert s['kept'] == ['yellow', 'white']
    assert s['available'] == ['orange', 'purple']
    assert s['regular_roll'] == 3
    act(engine, kind='select_die', die_id='orange')
    s = engine.snapshot()['state']
    assert s['available'] == []
    assert s['tray'] == ['blue', 'green', 'purple']
    assert engine.observe(active).data['sheets'][active]['blue'] == [5]


@pytest.mark.parametrize('available', [('green', 'purple'), ('purple',)])
def test_unusable_second_roll_keeps_same_dice_for_third_then_trays_them(monkeypatch, available):
    engine = regular([1, 1, 2, 1, 3, 1], available=available, roll=2, rerolls=1)
    active = engine.decision().player
    engine = restore(engine, lambda s: s['sheets'][active].update(green=3, purple=[5]))
    assert {a.kind for a in engine.legal_actions(active)} == {'reroll', 'decline_reroll'}
    from boardcraft.games.dice_circuit.randomness import KeyedRandom
    monkeypatch.setattr(KeyedRandom, 'die', lambda self, r, a, p, n, k, d: 2 if d == 'green' else 3)
    events = act(engine, kind='decline_reroll')
    rolls = [e for e in events if e.kind == 'dice_rolled']
    assert len(rolls) == 1
    assert set(rolls[0].payload['values']) == set(available)
    s = engine.snapshot()['state']
    assert s['available'] == list(available)
    assert s['regular_roll'] == 3
    act(engine, kind='decline_reroll')
    assert engine.snapshot()['state']['available'] == []
    assert set(engine.snapshot()['state']['tray']) == set(available)


def test_reroll_only_available_dice_keeps_opportunity_and_cannot_pass_legal_entry():
    engine = regular([2, 1, 3, 4, 5, 2], available=('orange', 'purple'), roll=2, rerolls=2)
    before = engine.snapshot()['state']
    assert 'decline_reroll' not in {a.kind for a in engine.legal_actions(engine.decision().player)}
    events = act(engine, kind='reroll')
    after = engine.snapshot()['state']
    assert after['regular_roll'] == 2
    assert after['reroll_no'] == 1
    assert after['sheets'][after['active']]['rerolls'] == 1
    assert all(after['dice'][d] == before['dice'][d] for d in ('yellow', 'blue', 'green', 'white'))
    assert set(next(e.payload['values'] for e in events if e.kind == 'dice_rolled')) == {'orange', 'purple'}


def test_shared_tray_not_consumed_and_extra_once_per_physical_die():
    engine = regular([1, 2, 3, 4, 6, 6], available=('orange',), roll=3, players=3)
    engine = restore(engine, lambda s: (s.update(tray=['yellow', 'blue', 'green', 'purple', 'white'], kept=[]), [x.update(extras=2,extras_earned=2) for x in s['sheets']]))
    active = engine.decision().player
    act(engine, kind='select_die', die_id='orange')
    act(engine, kind='extra_die', die_id='purple', area='purple')
    assert not any(a.kind == 'extra_die' and a.params['die_id'] == 'purple' for a in engine.legal_actions(active))
    act(engine, kind='finish_extra')
    passive = [p for p in range(3) if p != active]
    for p in passive:
        act(engine, player=p, kind='select_die', die_id='purple', area='purple')
        act(engine, player=p, kind='extra_die', die_id='purple', area='purple')
        assert not any(a.kind == 'extra_die' and a.params['die_id'] == 'purple' for a in engine.legal_actions(p))
    assert set(engine.snapshot()['state']['tray']) == {'yellow', 'blue', 'green', 'purple', 'white'}


def test_passive_free_kept_fallback_only_when_entire_tray_unusable():
    engine = regular([1, 1, 1, 6, 2, 6], available=('purple',), roll=3)
    active = engine.decision().player
    passive = 1 - active
    def fixture(s):
        s.update(tray=['yellow', 'blue', 'green'], kept=['orange', 'white'])
        s['sheets'][passive].update(yellow=[[r,c] for r in range(4) for c in range(4)], blue=list(range(2,13)), green=11, purple=[5])
    engine = restore(engine, fixture)
    act(engine, kind='select_die', die_id='purple')
    choices = engine.legal_actions(passive)
    assert choices and {a.params['die_id'] for a in choices} <= {'orange', 'white'}
    assert all(a.kind == 'select_die' for a in choices)


def test_new_extra_resource_usable_after_entry_chain_and_other_die_only():
    engine = regular([2, 2, 5, 4, 6, 3], available=('orange',), roll=3)
    active = engine.decision().player
    engine = restore(engine, lambda s: (s['sheets'][active].update(green=3, extras=1, extras_earned=1), s.update(tray=['yellow','blue','green','purple','white'], kept=[])))
    act(engine, kind='select_die', die_id='orange')
    act(engine, kind='extra_die', die_id='green', area='green')
    obs = engine.observe(active)
    assert obs.data['sheets'][active]['extras'] == 1
    assert (obs.data['sheets'][active]['extras_earned'], obs.data['sheets'][active]['extras_used']) == (2, 1)
    assert any(a.kind == 'extra_die' and a.params['die_id'] == 'purple' for a in engine.legal_actions(active))
    assert not any(a.kind == 'extra_die' and a.params['die_id'] == 'green' for a in engine.legal_actions(active))


def test_round_four_awards_every_player_once_and_finishes_chains_before_roll():
    engine = new(3)
    def fixture(s):
        s.update(round_no=3, turn_index=2, active=(s['starting']+2)%3, phase='passive', available=[], tray=list(DICE), kept=[])
        for w in s['windows']:
            w.update(stage='done', extra_used=[], stack={'version':1,'frames':[],'current':None})
        s['windows'][0]['stage'] = 'extra'
        s['sheets'][0]['extras'] = 1
        s['sheets'][0]['extras_earned'] = 1
        s['frozen_sheets'] = [dict(sheet) for sheet in s['sheets']]
    engine = restore(engine, fixture)
    events = act(engine, player=0, kind='finish_extra')
    assert engine.snapshot()['state']['round_no'] == 4
    assert not any(e.kind == 'dice_rolled' for e in events)
    for p in range(3):
        assert engine.observe(p).decision.kind == 'choose_round_bonus'
    for p in (2, 0, 1):
        events = act(engine, player=p, kind='choose_round_bonus', bonus='black6')
        assert not any(a.kind in ('reroll', 'extra_die') for a in engine.legal_actions(p))
        events = act(engine, player=p, kind='place_bonus', area='orange')
        assert engine.observe(p).data['sheets'][p]['earned_rewards'].count('round:4') == 1
        if p != 1:
            assert not any(e.kind == 'dice_rolled' for e in events)
    assert engine.snapshot()['state']['phase'] == 'regular'
    assert any(e.kind == 'dice_rolled' for e in events)


@pytest.mark.parametrize('config,seed', [(GameConfig('wrong','base',2),0), (GameConfig('dice_circuit','other',2),0), (GameConfig('dice_circuit','base',5),0), (GameConfig('dice_circuit','base',2),True), (GameConfig('dice_circuit','base',2),-1), (GameConfig('dice_circuit','base',2),2**64)])
def test_game_rejects_unsupported_configuration_and_seed(config, seed):
    with pytest.raises(ValueError):
        get_game('dice_circuit').new(config, seed)

def test_expired_last_bonus_completes_regular_opportunity_before_next_roll():
    engine = regular([1,1,1,1,1,1], roll=1)
    active = engine.decision().player
    engine = restore(engine, lambda s: (s['sheets'][active].update(blue=[6,10],green=11),s['dice'].update(blue=1,white=1)))
    act(engine, kind='select_die',die_id='blue',area='blue',target=2)
    assert engine.snapshot()['state']['regular_roll'] == 2
    assert engine.snapshot()['state']['windows'][active]['stage'] == 'regular'

@pytest.mark.parametrize('purple_first,added', [(True,13),(False,14)])
def test_engine_chain_order_preserves_locked_siblings_and_hand_derived_orange_score(purple_first, added):
    engine = regular([1,1,1,1,1,2], available=('blue',), roll=3)
    owner = engine.decision().player
    def fixture(s):
        s['sheets'][owner].update(blue=[2,4,7,11],orange=[1,2,3],purple=[1,2,3,4,5],
            yellow=[[0,3],[1,2],[2,1],[3,0],[1,0],[1,1]],
            earned_rewards=['orange:position:3','purple:position:3','purple:position:4','purple:position:5'])
    engine = restore(engine,fixture)
    act(engine,kind='select_die',die_id='blue',area='blue',target=3)
    if not purple_first:
        act(engine,kind='choose_bonus',bonus_id='blue:row:0')
    else:
        act(engine,kind='choose_bonus',bonus_id='blue:column:2')
    assert engine.clone().snapshot() == engine.snapshot()
    choices = engine.legal_actions(owner)
    assert len(choices) > 1 and {a.kind for a in choices} == {'place_bonus'}
    context = engine.observe(owner).data['window']['bonus_stack']
    assert context['current']['id'] == 'purple:position:6'
    assert context['current']['ancestry'] == ['blue:column:2']
    if purple_first:
        assert context['frames'][0]['pending'] == [{'source_id':'blue:row:0','kind':'orange5'}]
    act(engine,kind='place_bonus',area='yellow',target=[1,3])
    assert engine.observe(owner).data['window']['bonus_stack']['current']['id'] == 'orange:position:5'
    act(engine,kind='place_bonus',area='yellow',target=[0,0])
    orange = engine.observe(owner).data['sheets'][owner]['orange']
    assert sum(orange) - 6 == added
    assert orange[3:] == ([8,5] if purple_first else [10,4])


def test_no_reroll_auto_spends_failed_opportunities_with_same_available_dice(monkeypatch):
    engine = regular([2,1,5,1,5,1],available=('yellow','green','purple'))
    owner = engine.decision().player
    engine = restore(engine,lambda s:s['sheets'][owner].update(green=4,purple=[5]))
    from boardcraft.games.dice_circuit.randomness import KeyedRandom
    monkeypatch.setattr(KeyedRandom,'die',lambda self,r,a,p,n,k,d:2 if d == 'green' else 3)
    events = act(engine,kind='select_die',die_id='yellow',area='yellow',target=[1,0])
    rolls = [e.payload for e in events if e.kind == 'dice_rolled']
    assert [(x['opportunity'],x['values']) for x in rolls] == [(2,{'green':2,'purple':3}),(3,{'green':2,'purple':3})]
    assert engine.snapshot()['state']['tray'] == ['green','purple']
    assert engine.observe(owner).data['sheets'][owner]['purple'] == [5]

def test_any_usable_tray_entry_suppresses_free_kept_alternatives():
    engine = regular([1,1,1,6,2,6],available=('purple',),roll=3)
    active = engine.decision().player
    passive = 1-active
    def fixture(s):
        s.update(tray=['yellow','blue','green'],kept=['orange','white'])
        s['sheets'][passive].update(yellow=[[r,c] for r in range(4) for c in range(4)],blue=list(range(2,13)),green=0,purple=[5])
    engine = restore(engine,fixture)
    act(engine,kind='select_die',die_id='purple')
    assert {a.params['die_id'] for a in engine.legal_actions(passive)} == {'green'}


def test_final_passive_extra_entry_and_its_chain_finish_before_game_result():
    engine = new(2)
    def fixture(s):
        s.update(round_no=6,turn_index=1,active=(s['starting']+1)%2,phase='passive',available=[],tray=list(DICE),kept=[])
        for w in s['windows']:
            w.update(stage='done',extra_used=[],stack={'version':1,'frames':[],'current':None})
        s['windows'][0]['stage'] = 'extra'
        s['sheets'][0].update(extras=1,extras_earned=1,orange=[1,2,3,8],earned_rewards=['round:1','orange:position:3'])
        s['dice']['orange'] = 5
        s['frozen_sheets'] = [dict(sheet) for sheet in s['sheets']]
    engine = restore(engine,fixture)
    act(engine,player=0,kind='extra_die',die_id='orange',area='orange')
    assert not engine.is_over()
    assert engine.observe(0).decision.kind == 'place_bonus'
    assert engine.result() is None
    act(engine,player=0,kind='place_bonus',area='yellow',target=[0,0])
    assert engine.is_over()
    assert engine.result().area_scores[0]['orange'] == 19
