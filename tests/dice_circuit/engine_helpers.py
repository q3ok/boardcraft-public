"""Engine integration fixtures use controlled persistence, never runtime test hooks."""
import importlib.util
from boardcraft.contracts import GameConfig
from boardcraft.registry import get_game

DICE = ('yellow', 'blue', 'green', 'orange', 'purple', 'white')


def new(players=2, seed=0):
    assert importlib.util.find_spec('boardcraft.games.dice_circuit.engine') is not None, 'Full turn engine is missing'
    return get_game('dice_circuit').new(GameConfig('dice_circuit', 'base', players), seed)


def act(engine, player=None, kind=None, **params):
    if player is None:
        player = engine.decision().player
    choices = engine.legal_actions(player)
    action = next(a for a in choices if (kind is None or a.kind == kind) and all(a.params.get(k) == v for k, v in params.items()))
    return engine.apply(player, action, engine.observe(player).decision.id)


def regular(values, available=DICE, roll=1, rerolls=0, players=2):
    engine = new(players)
    snap = engine.snapshot()
    state = snap['state']
    state.update(phase='regular', dice=dict(zip(DICE, values)), available=list(available),
                 kept=[d for d in DICE if d not in available], tray=[], regular_roll=roll, reroll_no=0)
    state['sheets'][state['active']]['rerolls'] = rerolls
    state['sheets'][state['active']]['rerolls_earned'] = rerolls + state['sheets'][state['active']]['rerolls_used']
    return get_game('dice_circuit').restore(snap)


def restore(engine, change):
    snap = engine.snapshot()
    change(snap['state'])
    return get_game('dice_circuit').restore(snap)


def finish(engine, limit=3000):
    history = []
    for _ in range(limit):
        if engine.is_over():
            return history
        d = engine.decision()
        assert d is not None, 'Automatic progression deadlocked'
        actions = engine.legal_actions(d.player)
        assert actions
        action = next((a for a in actions if a.kind == 'finish_extra'), actions[0])
        history.append((d.player, action, d.id, engine.apply(d.player, action, d.id)))
    raise AssertionError('Game did not finish')
