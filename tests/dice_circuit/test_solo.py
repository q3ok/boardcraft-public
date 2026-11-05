"""Solo uses six independently rolled passive windows and a free fallback."""
from engine_helpers import DICE, act, finish, new, regular, restore


def test_solo_has_six_active_and_six_fresh_passive_turns_and_no_passive_reroll():
    engine = new(1, 71)
    events = list(engine.initial_events())
    events += [e for _,_,_,delta in finish(engine) for e in delta]
    active = [e for e in events if e.kind == 'turn_started' and not e.payload['simulated']]
    passive = [e for e in events if e.kind == 'turn_started' and e.payload['simulated']]
    assert len(active) == len(passive) == 6
    simulated = [e for e in events if e.kind == 'dice_rolled' and e.payload['phase'] == 'solo_passive']
    assert len(simulated) == 6
    assert all(set(e.payload['values']) == set(DICE) for e in simulated)
    assert engine.result().placements == (1,)


def test_solo_tie_proximity_and_free_remaining_three_fallback(monkeypatch):
    engine = regular([1,1,1,1,1,1], available=('orange',), roll=3, players=1)
    engine = restore(engine, lambda s: s['sheets'][0].update(yellow=[[r,c] for r in range(4) for c in range(4)], blue=list(range(2,13)),green=11,purple=[5]))
    from boardcraft.games.dice_circuit.randomness import KeyedRandom
    monkeypatch.setattr(KeyedRandom, 'die', lambda self,r,a,p,n,k,d: {'yellow':1,'blue':2,'purple':3,'green':5,'orange':6,'white':6}[d])
    act(engine, kind='select_die', die_id='orange')
    obs = engine.observe(0)
    assert obs.data['phase'] == 'passive'
    assert {d for d,v in obs.data['dice'].items() if v['zone'] == 'tray'} == {'yellow','blue','purple'}
    assert 'reroll' not in {a.kind for a in engine.legal_actions(0)}
    assert {a.params['die_id'] for a in engine.legal_actions(0)} <= {'orange','white','green'}
    assert any(a.params['die_id'] == 'orange' for a in engine.legal_actions(0))

def test_equal_faces_solo_tray_uses_seeded_proximity_and_no_legal_dice_does_not_deadlock(monkeypatch):
    from boardcraft.games.dice_circuit.randomness import KeyedRandom
    monkeypatch.setattr(KeyedRandom,'die',lambda *args:1)
    engine = regular([1,1,1,1,1,1],available=('orange',),roll=3,players=1)
    act(engine,kind='select_die',die_id='orange')
    expected = set(sorted(DICE,key=lambda d:KeyedRandom(0).proximity(1,0,d))[:3])
    assert set(engine.snapshot()['state']['tray']) == expected
    assert engine.clone().snapshot() == engine.snapshot()
    # All areas filled: the final active selection is itself a no-target chain;
    # the subsequent six-die passive roll is exhausted automatically.
    engine = new(1)
    def fixture(s):
        s.update(round_no=6,phase='active_extra',available=[],tray=list(DICE),kept=[])
        s['windows'][0].update(stage='extra',extra_used=[],stack={'version':1,'frames':[],'current':None})
        s['sheets'][0].update(yellow=[[r,c] for r in range(4) for c in range(4)],blue=list(range(2,13)),green=11,
            orange=[1,1,1,2,1,1,2,1,2,1,3],purple=[6]*11,extras=1,extras_earned=1)
        # Leave one legal extra entry, which also finishes the last passive turn.
        s['sheets'][0]['orange'].pop()
    engine = restore(engine,fixture)
    act(engine,kind='extra_die',die_id='orange',area='orange')
    assert engine.is_over()
