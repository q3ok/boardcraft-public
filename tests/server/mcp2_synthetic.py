"""Test-only hidden simultaneous game. Never added to boardcraft.registry."""
from dataclasses import asdict
from boardcraft.contracts import Action, Decision, Event, GameConfig, GameInfo, GameResult, Observation, PlayCapabilities
from boardcraft.server.service import PlayService


class Engine:
    def __init__(self, config, seed=0, done=None, inactive=None, cursors=None):
        self.config, self.secret = config, seed
        self.done = [] if done is None else list(done)
        self.inactive = [] if inactive is None else list(inactive)
        self.cursors = [0, 0, 0] if cursors is None else list(cursors)

    def decision(self):
        return next((self.observe(p).decision for p in range(2) if self.observe(p).decision), None)

    def observe(self, viewer):
        ready = viewer is not None and viewer not in self.done and viewer not in self.inactive
        decision = Decision(f'choice-{viewer}', viewer, 'choose') if ready else None
        # Only the acting seat sees its submission, never the other seat's secret.
        data = {'own_done': viewer in self.done, 'seat_active': viewer not in self.inactive}
        if viewer is not None:
            data['secret'] = self.secret + viewer
        return Observation('mcp2_synthetic', '1', '1', viewer, self.cursors[0 if viewer is None else viewer + 1], decision, data)

    def legal_actions(self, viewer):
        if self.observe(viewer).decision is None or viewer == 1:
            return ()
        return (Action('choose', 'choose', {}),)

    def command_specs(self, viewer):
        if viewer != 1 or self.observe(viewer).decision is None:
            return ()
        return ({'kind': 'choose', 'scope': 'decision', 'params_schema': {'type': 'object', 'properties': {}}},)

    def apply(self, player, action, decision_id):
        assert self.observe(player).decision.id == decision_id
        self.done.append(player)
        return (Event(1, 'private_choice', {'actor': player}),)

    def project_events(self, events, viewer):
        result = []
        for event in events:
            if viewer == event.payload['actor']:
                index = viewer + 1
                self.cursors[index] += 1
                result.append(Event(self.cursors[index], 'chosen', {}))
        return tuple(result)

    def initial_events(self): return ()
    def is_over(self): return len(self.done) == 2
    def result(self): return GameResult((1, 1), ((), ()), (0, 0), (1, 1), 'done') if self.is_over() else None
    def snapshot(self):
        return {'config': asdict(self.config), 'seed': self.secret, 'done': self.done, 'inactive': self.inactive, 'cursors': self.cursors}


class Module:
    game_id = 'mcp2_synthetic'
    rules_version = engine_version = observation_version = action_version = '1'
    def info(self): return GameInfo(self.game_id, '1', '1', 'Synthetic', ('base',), 2, 2, (), PlayCapabilities(parameterized_actions=True))
    def new(self, config, seed): return Engine(config, seed)
    def restore(self, snapshot):
        return Engine(GameConfig(**snapshot['config']), snapshot['seed'], snapshot['done'], snapshot['inactive'], snapshot['cursors'])
    def settings_schema(self): return {'type': 'object', 'properties': {}, 'additionalProperties': False}
    def describe_action(self, action): return 'Choose'


class Catalog:
    def list_bots(self, game_id): return ()


class SyntheticService(PlayService):
    @staticmethod
    def module(game_id):
        return Module() if game_id == 'mcp2_synthetic' else PlayService.module(game_id)


def table(tmp_path, **kwargs):
    service = SyntheticService(tmp_path, seed_factory=lambda: 37, bot_catalog=Catalog(), clock=lambda: 1000., **kwargs)
    made = service.create_game(GameConfig('mcp2_synthetic', 'base', 2), ['human', 'human'])
    tokens = [service.join_game(made['table_id'], p)['credential'] for p in range(2)]
    service.start_game(made['table_id'], made['creator_credential'])
    return service, made['table_id'], tokens
