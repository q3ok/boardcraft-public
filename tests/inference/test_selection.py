"""Model play is greedy everywhere; historical sampled references are read-only."""
import asyncio
import time
import numpy as np
import pytest
from boardcraft.contracts import GameConfig
from boardcraft.registry import get_game
from boardcraft.inference.bot import InferenceBot


def test_greedy_is_masked_argmax_independent_of_seed():
    module = get_game('dice_circuit')
    engine = module.new(GameConfig('dice_circuit', 'base', 1), 19)
    obs = engine.observe(0)
    actions = engine.legal_actions(0)
    encoder = module.encoder()
    mask = encoder.mask(obs, actions)
    logits = np.linspace(-1, 1, len(mask), dtype=np.float32)
    logits[~mask] = 999
    expected = int(np.where(mask, logits, -np.inf).argmax())
    class Policy:
        def logits(self, encoded): return logits
        def probs(self, *args): pytest.fail('Greedy must use logits, without sampling')
    for seed in range(12):
        bot = InferenceBot(encoder, Policy(), seed)
        assert bot.sampling_version == 'numpy-argmax-v1'
        assert encoder.encode_action(bot.choose(obs, actions).action) == expected


def test_model_table_is_greedy_and_historical_identity_is_read_only(tmp_path):
    from test_catalog import artifact
    from boardcraft.inference.catalog import RuntimeBotCatalog, NumpySource, reference_data, reference_from_data
    from boardcraft.server.service import PlayService
    from boardcraft.server.schemas import CreateInput
    paths = artifact(tmp_path / 'artifact')
    catalog = RuntimeBotCatalog({'fixture': NumpySource(*paths)})
    from boardcraft.inference.catalog import with_model_selection, SAMPLING_VERSION, BotIncompatible
    greedy = catalog.pin('dice_circuit', 'fixture')
    assert greedy.sampling_version == 'numpy-argmax-v1'
    sample = with_model_selection(greedy, SAMPLING_VERSION)
    assert reference_from_data(reference_data(sample)) == sample
    with pytest.raises(BotIncompatible): catalog.resolve(sample, 0)
    play = PlayService(tmp_path / 'play', bot_catalog=catalog)
    request = CreateInput.model_validate(dict(config=dict(game_id='dice_circuit', variant='base', players=1),
        seats=['fixture']))
    made = play.create_game(request.config.game_config(), request.model_dump()['seats'])
    greedy = reference_from_data(made['seats'][0]['bot_ref'])
    assert greedy.sampling_version == 'numpy-argmax-v1'
    assert greedy.identity != sample.identity and greedy.policy_sha256 == sample.policy_sha256
    assert reference_from_data(reference_data(greedy)) == greedy
    play.start_game(made['table_id'], made['creator_credential'])
    for work in play.bot_work(made['table_id']):
        bot = catalog.resolve(greedy, work.seed)
        mask = bot.encoder.mask(work.observation, work.actions)
        expected = np.where(mask, bot.policy.logits(bot.encoder.encode(work.observation)), -np.inf).argmax()
        assert bot.encoder.encode_action(play.choose_bot(work).action) == expected


def test_bundled_rentcraft_models_keep_their_greedy_identity(tmp_path):
    from boardcraft.inference.catalog import default_catalog
    from boardcraft.server.service import PlayService
    catalog = default_catalog()
    models = [d for d in catalog.list_bots('rentcraft') if d.kind == 'model']
    assert len(models) == 3 and all(d.reference.sampling_version == 'rentcraft-greedy-v1' for d in models)
    play = PlayService(tmp_path/'play', bot_catalog=catalog)
    alias = models[0].choice_id
    made = play.create_game(GameConfig('rentcraft', 'base', 2), [alias, alias])
    assert made['seats'][0]['bot_ref'] == made['seats'][1]['bot_ref']
    assert all('greedy' in (model.description or '').lower() for model in models)


def test_manifest_can_pin_greedy_without_changing_artifact_metadata(tmp_path):
    import json
    from test_catalog import artifact
    from boardcraft.inference.catalog import default_catalog, _manifest_sources
    meta, weights = artifact(tmp_path/'artifact')
    manifest = tmp_path/'models.json'
    manifest.write_text(json.dumps(dict(version=1,models=[dict(choice_id='fixture',metadata=str(meta),weights=str(weights),sampling_version='numpy-argmax-v1')])),encoding='utf-8')
    catalog = default_catalog(manifest)
    reference = catalog.pin('dice_circuit','fixture')
    assert reference.sampling_version == 'numpy-argmax-v1'
    assert catalog.resolve(reference,0).sampling_version == 'numpy-argmax-v1'
    manifest.write_text('{"version":1,"models":[5]}',encoding='utf-8')
    with pytest.raises(ValueError): _manifest_sources(manifest)


@pytest.mark.parametrize('mode',['greedy','sample'])
def test_table_and_tournament_schemas_do_not_offer_model_selection(mode):
    from pydantic import ValidationError
    from boardcraft.server.schemas import CreateInput
    from boardcraft.server.tournaments import TournamentInput
    config = dict(game_id='dice_circuit',variant='base',players=1)
    seats = [dict(choice_id='fixture',selection=mode)]
    with pytest.raises(ValidationError): CreateInput.model_validate(dict(config=config,seats=seats))
    with pytest.raises(ValidationError):
        TournamentInput.model_validate(dict(name='Fixture',config=config,seats=seats,rules_version='1',engine_version='1',
            seeds=[11],sampler_seeds=[[21]],rotations=[0],match_count=1,schedule_cap=1,max_inflight=1,
            max_record_bytes=1000000,max_decisions=1000))


def test_tournament_model_plays_argmax_on_every_move(tmp_path):
    from test_catalog import artifact
    from boardcraft.inference.catalog import RuntimeBotCatalog, NumpySource
    from boardcraft.server.service import PlayService
    catalog = RuntimeBotCatalog({'fixture':NumpySource(*artifact(tmp_path/'artifact'))})
    actual_resolve = catalog.resolve
    accepted = []
    def checked_resolve(reference, seed):
        bot = actual_resolve(reference,seed)
        choose = bot.choose
        def checked_choose(obs,actions):
            mask=bot.encoder.mask(obs,actions)
            expected=int(np.where(mask,bot.policy.logits(bot.encoder.encode(obs)),-np.inf).argmax())
            choice=choose(obs,actions)
            assert bot.encoder.encode_action(choice.action)==expected
            accepted.append(expected)
            return choice
        bot.choose=checked_choose
        return bot
    catalog.resolve=checked_resolve
    play=PlayService(tmp_path/'play',bot_catalog=catalog)
    module=get_game('dice_circuit')
    made=play.create_tournament(dict(name='Argmax',config=dict(game_id='dice_circuit',variant='base',players=1),
        seats=['fixture'],rules_version=module.rules_version,engine_version=module.engine_version,seeds=[11],
        sampler_seeds=[[21]],rotations=[0],match_count=1,schedule_cap=1,max_inflight=1,max_record_bytes=1000000,max_decisions=1000))
    try:
        play.start_tournament(made['tournament_id'],made['creator_credential'],made['spec_sha'],0)
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            state=play.get_tournament(made['tournament_id'])
            if state['status'] not in ('running','draining'): break
            time.sleep(.02)
        assert state['status']=='completed',state
        assert len(accepted)>20
        assert state['spec']['references'][0]['sampling_version']=='numpy-argmax-v1'
    finally: play.close_tournaments()


def test_mcp_model_discovery_and_creation_only_advertise_greedy(tmp_path):
    import json
    from test_catalog import artifact
    from boardcraft.inference.catalog import RuntimeBotCatalog, NumpySource
    from boardcraft.server.service import PlayService
    from boardcraft.mcp.server import create_server
    from mcp.client import Client
    service=PlayService(tmp_path/'play',bot_catalog=RuntimeBotCatalog({'fixture':NumpySource(*artifact(tmp_path/'artifact'))}))
    async def run():
        async with Client(create_server(service)) as client:
            listed=await client.call_tool('list_bots',{'game_id':'dice_circuit'})
            bots=json.loads(listed.content[0].text)
            assert next(bot for bot in bots if bot['choice_id']=='fixture')['reference']['sampling_version']=='numpy-argmax-v1'
            made=await client.call_tool('create_game',dict(config=dict(game_id='dice_circuit',variant='base',players=1),seats=['fixture']))
            assert not made.is_error
            assert json.loads(made.content[0].text)['seats'][0]['bot_ref']['sampling_version']=='numpy-argmax-v1'
            bad=await client.call_tool('create_game',dict(config=dict(game_id='dice_circuit',variant='base',players=1),
                seats=[dict(choice_id='fixture',selection='sample')]))
            assert bad.is_error
    asyncio.run(run())
