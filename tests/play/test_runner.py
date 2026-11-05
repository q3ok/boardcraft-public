"""The shared runner projects each receipt before advancing any engine."""
import importlib.util
from dataclasses import replace
import pytest
from boardcraft.contracts import Action, Decision, Event, GameConfig, GameResult, Observation


def test_runner_accepts_non_dice_module_and_bots_only_receive_observation_and_legals():
    assert importlib.util.find_spec('boardcraft.play') is not None, 'Generic game runner is missing'
    from boardcraft.contracts import BotChoice
    from boardcraft.play.runner import run_game
    from boardcraft.play.replay import replay
    move = Action('go', 'go', {})
    from boardcraft.games.dice_circuit.scoring import DiceCircuitResult
    result = DiceCircuitResult((7,), ({'yellow': 7, 'blue': 0, 'green': 0, 'orange': 0, 'purple': 0},), (0,), (1,), 'completed')
    class Tiny:
        game_id = rules_version = engine_version = 'tiny'
        def new(self, config, seed):
            self.n = 0
            self._config = config
            return self
        @property
        def config(self):
            return self._config
        def restore(self, saved):
            return Tiny().new(GameConfig('tiny', 'base', 1), 0)._restore(saved)
        def _restore(self, saved):
            self.n = saved['n']
            return self
        def initial_events(self):
            return (Event(1, 'start', {}),)
        def project_events(self, events, viewer):
            assert events == (self.initial_events() if not self.n else (Event(2, 'end', {}),))
            return events
        def decision(self):
            return None if self.n else Decision('pick', 0, 'go')
        def observe(self, viewer):
            return Observation('tiny', 'tiny', '1', viewer, self.n, None if viewer is None else self.decision(), {'done': bool(self.n)})
        def legal_actions(self, player):
            return () if self.n else (move,)
        def apply(self, player, action, decision_id):
            assert (player, action, decision_id) == (0, move, 'pick')
            self.n += 1
            return (Event(2, 'end', {}),)
        def snapshot(self):
            return {'n': self.n}
        def is_over(self):
            return bool(self.n)
        def result(self):
            return result if self.n else None
    class OnlyObservationBot:
        def choose(self, observation, legal_actions):
            assert observation.viewer == 0 and legal_actions == (move,)
            return BotChoice(move, 'Choose the visible legal move.')
    module = Tiny()
    record = run_game(module, GameConfig('tiny', 'base', 1), 0, [OnlyObservationBot()])
    assert record.final_result == result and len(record.decisions) == 1
    assert replay(record, module=Tiny()).snapshot() == {'n': 1}
    mismatched = replace(record, config=GameConfig('tiny', 'other-variant', 1))
    with pytest.raises(ValueError, match='configuration'):
        replay(mismatched, through_decision=0, module=Tiny())
