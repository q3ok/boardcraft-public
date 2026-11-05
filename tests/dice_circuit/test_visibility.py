"""Private passive branches and stable perspective-local metadata."""
from dataclasses import asdict
from engine_helpers import DICE, act, new, regular, restore


def passive_fixture():
    engine = regular([1,2,3,4,6,6], available=('orange',), roll=3, players=4)
    active = engine.decision().player
    engine = restore(engine, lambda s: s.update(tray=['yellow','blue','green','purple','white'],kept=[]))
    act(engine, kind='select_die', die_id='orange')
    return engine, active, [p for p in range(4) if p != active]


def test_any_passive_seat_can_act_and_entire_untouched_payload_is_frozen():
    engine, active, passive = passive_fixture()
    first, second, third = passive
    untouched = {p: asdict(engine.observe(p)) for p in (active, second, third, None)}
    d = engine.observe(first).decision
    assert engine.observe(second).decision is not None
    events = act(engine, player=first, kind='select_die', die_id='purple', area='purple')
    assert engine.observe(first).data['sheets'][first]['purple'] == [6]
    for viewer, before in untouched.items():
        assert asdict(engine.observe(viewer)) == before
        assert engine.project_events(events, viewer) == ()
    projected = engine.project_events(events, first)
    assert projected and all(e.sequence <= engine.observe(first).data['event_cursor'] for e in projected)
    other = engine.clone()
    act(engine, player=third, kind='select_die', die_id='purple', area='purple')
    act(engine, player=second, kind='select_die', die_id='purple', area='purple')
    act(other, player=second, kind='select_die', die_id='purple', area='purple')
    act(other, player=third, kind='select_die', die_id='purple', area='purple')
    assert [x['purple'] for x in engine.observe(None).data['sheets']] == [x['purple'] for x in other.observe(None).data['sheets']]
    assert engine.observe(None) == other.observe(None)


def test_different_other_branch_lengths_do_not_change_personal_decision_or_cursor():
    engine, active, passive = passive_fixture()
    owner, viewer, last = passive
    def fixture(s):
        s['sheets'][owner].update(blue=[2,4,7,11],orange=[1,2,3],purple=[1,2,3,4,5],
            earned_rewards=['orange:position:3','purple:position:3','purple:position:4','purple:position:5'])
        s['dice'].update(blue=1,white=2)
    engine = restore(engine, fixture)
    baseline = asdict(engine.observe(viewer))
    act(engine, player=owner, kind='select_die', die_id='blue', area='blue', target=3)
    assert asdict(engine.observe(viewer)) == baseline
    act(engine, player=owner, kind='choose_bonus', bonus_id='blue:column:2')
    own = engine.observe(owner).data['window']['bonus_stack']
    assert any(x['source_id'] == 'blue:row:0' for f in own['frames'] for x in f['pending'])
    assert own['current']['id'] == 'purple:position:6'
    assert asdict(engine.observe(viewer)) == baseline
    assert engine.observe(owner).data['window']['bonus_stack']['frames'][-1]['ancestry'] == ['blue:column:2']
    assert asdict(engine.observe(viewer)) == baseline
    restored = engine.clone()
    assert restored.observe(owner) == engine.observe(owner)
    assert restored.observe(viewer) == engine.observe(viewer)


def test_observations_and_safe_events_contain_no_seed_rng_or_hidden_future():
    import json
    engine = new(3, 987654321)
    for viewer in (0,1,2,None):
        payload = json.dumps(asdict(engine.observe(viewer))).lower()
        assert all(token not in payload for token in ('seed','random','987654321','proximity','reroll_no'))
        projected = engine.project_events(engine.initial_events(), viewer)
        assert projected == engine.project_events(engine.initial_events(), viewer)
        encoded = json.dumps([asdict(e) for e in projected]).lower()
        assert all(token not in encoded for token in ('seed','random','987654321','proximity'))

def test_round_four_simultaneous_choices_freeze_entire_other_perspectives_until_barrier():
    engine = new(4)
    def fixture(s):
        s.update(round_no=4,phase='round_bonus',available=[],tray=list(DICE),kept=[])
        for w in s['windows']:
            w.update(stage='round',extra_used=[],stack={'version':1,'frames':[],'current':None})
        s['frozen_sheets'] = [dict(sheet) for sheet in s['sheets']]
    engine = restore(engine,fixture)
    before = {p:asdict(engine.observe(p)) for p in (1,2,3,None)}
    for kind,params in [('choose_round_bonus',{'bonus':'black6'}),('place_bonus',{'area':'orange'})]:
        events = act(engine,player=0,kind=kind,**params)
        for p in before:
            assert asdict(engine.observe(p)) == before[p]
            assert engine.project_events(events,p) == ()
    assert engine.observe(0).data['sheets'][0]['orange'] == [6]
    assert engine.observe(None).data['sheets'][0]['orange'] == []
    for p in (3,2,1):
        act(engine,player=p,kind='choose_round_bonus',bonus='black6')
        events = act(engine,player=p,kind='place_bonus',area='orange')
    assert engine.observe(None).data['phase'] == 'regular'
    assert [x['orange'] for x in engine.observe(None).data['sheets']] == [[6],[6],[6],[6]]
    assert any(e.kind == 'window_published' for e in engine.project_events(events,None))
