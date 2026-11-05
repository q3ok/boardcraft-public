"""Stateless keyed draws, bootstrap receipts and controlled snapshot replay."""
from copy import deepcopy
from dataclasses import asdict
import importlib.util
import json
import pytest
from boardcraft.contracts import Action, IllegalAction, StaleDecision, WrongPlayer
from boardcraft.registry import get_game
from engine_helpers import DICE, act, finish, new, regular


def test_bootstrap_roll_once_idempotent_receipt_and_restore_does_not_redraw(monkeypatch):
    engine = new(2, 11)
    events = engine.initial_events()
    assert len([e for e in events if e.kind == 'dice_rolled']) == 1
    assert {'game_started','round_started','turn_started','dice_rolled'} <= {e.kind for e in events}
    assert events == engine.initial_events()
    from boardcraft.games.dice_circuit.randomness import KeyedRandom
    monkeypatch.setattr(KeyedRandom, 'die', lambda *args: (_ for _ in ()).throw(AssertionError('Unexpected redraw')))
    restored = get_game('dice_circuit').restore(json.loads(json.dumps(engine.snapshot())))
    assert restored.initial_events() == events
    assert restored.snapshot() == engine.snapshot()
    assert restored.observe(None) == engine.observe(None)
    first = engine.initial_events()[0]
    first.payload['changed'] = True
    assert 'changed' not in engine.initial_events()[0].payload


@pytest.mark.parametrize('players', [1,2,3,4])
def test_complete_deterministic_games_snapshot_clone_and_final_results(players):
    engine = new(players, 29)
    copy = engine.clone()
    history = finish(engine)
    for player, action, did, events in history:
        assert copy.observe(player).decision.id == did
        assert copy.apply(player,action,did) == events
    assert copy.snapshot() == engine.snapshot()
    assert copy.result() == engine.result()
    assert engine.decision() is None
    assert all(engine.observe(p).decision is None for p in range(players))
    assert engine.result() is not None
    assert engine.result().reason == 'completed'
    restored = get_game('dice_circuit').restore(engine.snapshot())
    assert restored.snapshot() == engine.snapshot()
    assert restored.result() == engine.result()
    assert len(json.dumps(engine.snapshot())) < 60000
    assert engine.initial_events() == new(players,29).initial_events()


def test_illegal_stale_wrong_player_are_transactional_and_clone_independent():
    engine = new(2)
    d = engine.decision()
    action = engine.legal_actions(d.player)[0]
    before = engine.snapshot()
    for p,a,did,error in [(d.player,action,'old',StaleDecision),(1-d.player,action,d.id,WrongPlayer),
                         (d.player,Action('bad','discard_die',{}),d.id,IllegalAction),
                         (d.player,Action(action.id, action.kind, {'tampered': True}),d.id,IllegalAction)]:
        with pytest.raises(error):
            engine.apply(p,a,did)
        assert engine.snapshot() == before
    clone = engine.clone()
    clone.apply(d.player,action,d.id)
    assert engine.snapshot() == before
    assert clone.snapshot() != before


def test_keyed_random_common_dice_and_separate_domains():
    assert importlib.util.find_spec('boardcraft.games.dice_circuit.randomness') is not None, 'Keyed randomness is missing'
    from boardcraft.games.dice_circuit.randomness import KeyedRandom
    rng = KeyedRandom(483)
    values = [rng.die(2,1,'active',3,0,d) for d in DICE]
    assert all(1 <= v <= 6 for v in values)
    assert values == [KeyedRandom(483).die(2,1,'active',3,0,d) for d in DICE]
    assert len({rng.die(2,1,'active',3,n,'white') for n in range(40)}) > 1
    assert len({rng.proximity(2,1,d) for d in DICE}) == 6
    assert 0 <= rng.starting_seat(4) < 4
    one = regular([1,1,1,1,1,1])
    two = one.clone()
    act(one,kind='select_die',die_id='yellow',area='yellow')
    act(two,kind='select_die',die_id='orange',area='orange')
    a,b = one.snapshot()['state'],two.snapshot()['state']
    common = set(a['available']) & set(b['available'])
    assert common
    assert all(a['dice'][d] == b['dice'][d] for d in common)


@pytest.mark.parametrize('mutation', [lambda s:s.update(format=77),lambda s:s.update(engine_version='new'),
    lambda s:s['state'].update(round_no=0),lambda s:s['state']['dice'].update(white=7),
    lambda s:s['state']['sheets'][0].update(extras=-1),lambda s:s['state'].update(phase='unknown')])
def test_snapshot_corruption_and_unknown_versions_rejected(mutation):
    engine = new(2)
    snap = engine.snapshot()
    mutation(snap)
    with pytest.raises(ValueError):
        get_game('dice_circuit').restore(snap)

def test_action_parameters_cannot_alias_integer_targets_with_booleans():
    engine = new(2)
    d = engine.decision()
    action = next(a for a in engine.legal_actions(d.player) if a.kind == 'select_die' and a.params['area'] == 'orange')
    before = engine.snapshot()
    for value in (False,0.0):
        params = dict(action.params, target=value)
        with pytest.raises(IllegalAction):
            engine.apply(d.player,Action(action.id,action.kind,params),d.id)
        assert engine.snapshot() == before


def test_safe_receipts_are_detached_exact_and_expire_after_next_accepted_apply():
    engine = new(2)
    first = act(engine,kind='select_die',die_id='orange')
    baseline = engine.snapshot()
    viewer = engine.snapshot()['state']['active']
    projected = engine.project_events(first,viewer)
    assert engine.snapshot() == baseline
    projected[0].payload['tampered'] = True
    assert 'tampered' not in engine.project_events(first,viewer)[0].payload
    altered = list(deepcopy(first))
    altered[0].payload['tampered'] = True
    with pytest.raises(ValueError):
        engine.project_events(tuple(altered),viewer)
    engine.observe(viewer).data['sheets'][viewer]['orange'].append(999)
    changed = engine.snapshot()
    changed['state']['sheets'][viewer]['orange'].append(999)
    assert engine.snapshot() == baseline
    act(engine)
    with pytest.raises(ValueError):
        engine.project_events(first,viewer)
    assert engine.project_events(engine.initial_events(),viewer)

def test_exact_receipt_projection_rejects_bool_number_aliases():
    from boardcraft.contracts import Event
    engine = new(1)
    original = engine.initial_events()
    first = original[0]
    payload = deepcopy(dict(first.payload))
    payload['config']['players'] = True
    altered = (Event(first.sequence,first.kind,payload),) + original[1:]
    with pytest.raises(ValueError):
        engine.project_events(altered,None)
    altered = (Event(True,first.kind,first.payload),) + original[1:]
    with pytest.raises(ValueError):
        engine.project_events(altered,None)

def test_snapshot_and_clone_keep_bounded_receipts_after_many_rerolls():
    engine = regular([2,2,3,4,5,2],rerolls=7)
    act(engine,kind='reroll')
    short_size = len(json.dumps(engine.snapshot()))
    for _ in range(150):
        # Refill this synthetic stress fixture without overflowing the printed track.
        if not engine.snapshot()['state']['sheets'][engine.decision().player]['rerolls']:
            saved = engine.snapshot()
            saved['state']['sheets'][engine.decision().player].update(rerolls=7, rerolls_earned=7, rerolls_used=0)
            engine = get_game('dice_circuit').restore(saved)
        act(engine,kind='reroll')
    long_size = len(json.dumps(engine.snapshot()))
    assert long_size < short_size + 150
    assert len(engine.snapshot()['receipt']['events']) == 2
    assert engine.clone().snapshot() == engine.snapshot()

@pytest.mark.parametrize('mutation', [lambda s:s['state'].update(regular_roll=0),
    lambda s:s['state'].update(active=1-s['state']['active']),
    lambda s:s['state']['windows'][s['state']['active']].update(stage='done'),
    lambda s:s['presentation'].update(sequence=0),
    lambda s:s['receipt']['events'][0].update(sequence=9999)])
def test_snapshot_cross_field_corruption_is_rejected(mutation):
    engine = new(2)
    snap = engine.snapshot()
    mutation(snap)
    with pytest.raises(ValueError):
        get_game('dice_circuit').restore(snap)

@pytest.mark.parametrize('player', [-1,2,None,True])
def test_invalid_or_observer_actor_cannot_mutate_game(player):
    engine = new(2)
    d = engine.decision()
    before = engine.snapshot()
    with pytest.raises(WrongPlayer):
        engine.apply(player,engine.legal_actions(d.player)[0],d.id)
    assert engine.snapshot() == before

@pytest.mark.parametrize('corruption', ['passive_phase','premature_over','stranded_bonus_frame'])
def test_review_restore_rejects_deadlocked_or_premature_snapshot(corruption, monkeypatch):
    from boardcraft.games.dice_circuit.randomness import KeyedRandom
    engine = new(2,0)
    saved = engine.snapshot()
    if corruption == 'stranded_bonus_frame':
        saved['state']['windows'][saved['state']['active']]['stack']['frames'].append({'pending':[],'ancestry':[]})
    else:
        saved['state']['phase'] = 'passive' if corruption == 'passive_phase' else 'over'
    before = deepcopy(saved)
    monkeypatch.setattr(KeyedRandom,'die',lambda *args:(_ for _ in ()).throw(AssertionError('Restore must not draw')))
    with pytest.raises(ValueError):
        get_game('dice_circuit').restore(saved)
    assert saved == before

@pytest.mark.parametrize('phase', ['passive','round_bonus'])
def test_simultaneous_restore_requires_frozen_view_and_finished_dice_roll(phase):
    engine = new(3,29)
    for _ in range(1000):
        if engine.snapshot()['state']['phase'] == phase:
            break
        choices = engine.legal_actions(engine.decision().player)
        selected = next((a for a in choices if a.kind == 'finish_extra'),choices[0])
        engine.apply(engine.decision().player,selected,engine.decision().id)
    else:
        raise AssertionError('Expected simultaneous phase was not reached')
    saved = engine.snapshot()
    assert get_game('dice_circuit').restore(saved).snapshot() == saved
    for corruption in ('frozen_view','available_die'):
        bad = deepcopy(saved)
        if corruption == 'frozen_view':
            bad['state']['frozen_sheets'] = None
        else:
            die = bad['state']['tray'].pop()
            bad['state']['available'].append(die)
        with pytest.raises(ValueError):
            get_game('dice_circuit').restore(bad)
