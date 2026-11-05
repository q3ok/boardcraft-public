"""Run any explicit game module with observation-only bots."""
from boardcraft.contracts import Action, BotChoice, GameConfig
from boardcraft.settings import normalize_settings
from .records import GameRecord, ParticipantRecord, RecordedDecision, RecordedOperation, validate_participants


def project_receipt(engine, events, players):
    """Consume all permitted projections while this exact receipt is current."""
    for viewer in (*range(players), None):
        engine.project_events(events, viewer)


def record_version(module):
    if hasattr(module, 'info'):
        caps = module.info().capabilities
        if caps.seat_operations or caps.parameterized_actions:
            return '3'
    return '2'


def simulation_config(module, config):
    """Freeze simulation defaults once, retaining explicitly chosen table defaults."""
    if not hasattr(module, 'simulation_defaults'):
        return config
    defaults = module.simulation_defaults()
    schema = module.settings_schema()
    overrides = normalize_settings(schema, {**defaults, **config.settings})
    # If the simulation default differs from the table default, the latter must
    # remain explicit when requested (for example an unlimited round count).
    for key in defaults.keys() - overrides.keys():
        overrides[key] = schema['properties'][key]['default']
    return GameConfig(config.game_id, config.variant, config.players, overrides)


def run_game(module, config, seed, bots, *, participants=None) -> GameRecord:
    return run_configured_game(module, simulation_config(module, config), seed, bots, participants=participants)


def run_configured_game(module, config, seed, bots, *, participants=None, max_steps=None) -> GameRecord:
    """Run the supplied frozen rules without applying any new-game defaults."""
    if len(bots) != config.players:
        raise ValueError('Provide exactly one bot for each player')
    if participants is None:
        participants = tuple(ParticipantRecord(seat, 'external') for seat in range(config.players))
    validate_participants(participants, config, module.rules_version)
    engine = module.new(config, seed)
    for seat, bot in enumerate(bots):
        initialize = getattr(bot, "initialize", None)
        if callable(initialize):
            initialize(engine.observe(seat))
    initial_snapshot = engine.snapshot()
    initial_events = engine.initial_events()
    project_receipt(engine, initial_events, config.players)
    version = record_version(module)
    steps = []
    while not engine.is_over():
        if max_steps is not None and len(steps) >= max_steps:
            raise ValueError('Tournament decision limit')
        decision = engine.decision()
        if decision is None:
            raise ValueError('Game has no decision before completion')
        observation = engine.observe(decision.player)
        if observation.decision != decision:
            raise ValueError('Selected decision disagrees with the player observation')
        legal = engine.legal_actions(decision.player)
        choice = bots[decision.player].choose(observation, legal)
        if not isinstance(choice, BotChoice) or not isinstance(choice.explanation, str) or not choice.explanation:
            raise ValueError('Bot must return an action and a nonempty explanation')
        events = engine.apply(decision.player, choice.action, decision.id)
        project_receipt(engine, events, config.players)
        accepted = Action(choice.action.id, choice.action.kind, choice.action.params)
        if version == '3':
            steps.append(RecordedOperation(decision.player, decision, observation.revision, accepted, events,
                                           choice.explanation, meta=choice.meta))
        else:
            steps.append(RecordedDecision(decision, accepted, events, choice.explanation, meta=choice.meta))
    result = engine.result()
    if result is None:
        raise ValueError('Completed game has no final result')
    return GameRecord(version, config, module.rules_version, module.engine_version, initial_snapshot,
                      initial_events, tuple(steps) if version == '2' else (), True, result, participants,
                      tuple(steps) if version == '3' else ())
