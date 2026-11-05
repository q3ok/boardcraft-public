"""Combined players preserve routing, immutable component pins and seat state."""
import asyncio
import copy
import importlib
import json
import zipfile
from dataclasses import replace

import numpy as np
import pytest
from boardcraft.contracts import Action, BotChoice, GameConfig
from boardcraft.registry import get_game
from boardcraft.inference import catalog as api
from boardcraft.games.rentcraft.actions import Phase
from boardcraft.games.rentcraft.inference.policy import NumpyPolicy
from boardcraft.games.rentcraft.inference.profiles import MINI, V7
from boardcraft.games.rentcraft.inference.policy import weight_shapes
from boardcraft.games.rentcraft.inference.profiles import resolve_profile


def policy(profile_id):
    profile = resolve_profile(profile_id)
    net = {"family":"rentcraft-legacy-mlp-v1", "hidden":[2], "activation":"relu", "selection":"greedy"}
    arrays = {k:np.zeros(s, np.float32) for k,s in weight_shapes(profile, net).items()}
    return profile, NumpyPolicy(profile, net, arrays)


def fresh():
    return get_game("rentcraft").new(GameConfig("rentcraft", "base", 2), 19)


def hybrid():
    spec = importlib.util.find_spec("boardcraft.games.rentcraft.inference.hybrid")
    assert spec is not None, "Rentcraft hybrid player is missing"
    return importlib.import_module(spec.name)


@pytest.mark.parametrize("spec", [
    "miser", "hybrid:", "hybrid:buy=miser", "hybrid:unknown=miser;rest=miser",
    "hybrid:buy=;rest=miser", "hybrid:buy=miser;buy=random;rest=miser",
    "hybrid:rest=hybrid:rest=miser", "hybrid:buy;rest=miser",
    "hybrid:reszta=miser", "hybrid:trades=miser;rest=miser",
])
def test_parser_refuses_missing_unknown_duplicate_and_nested_owners(spec):
    with pytest.raises(ValueError):
        hybrid().parse_hybrid(spec)


def test_parser_splits_first_equals_and_expands_rest():
    assert hybrid().parse_hybrid(" hybrid: BUY=miser:buy_reserve=200; offers=synthetic; rest=random; ") == {
        "buy": "miser:buy_reserve=200", "auctions": "random", "management": "random",
        "offers": "synthetic", "responses": "random"}


class Player:
    def __init__(self, kind, calls, name):
        self.kind, self.calls, self.name = kind, calls, name
    def choose(self, observation, legal_actions):
        self.calls.append(self.name)
        return BotChoice(Action(self.kind, self.kind, {}), self.name)


class Model(Player):
    def choose(self, observation, legal_actions, *, allow_trades=True):
        self.calls.append((self.name, allow_trades))
        return BotChoice(Action(self.kind, self.kind, {}), self.name)


@pytest.mark.parametrize("phase,area", [
    ("buy_decision", "buy"), ("auction", "auctions"), ("trade_response", "responses"),
    ("trade_window", "offers"), ("pre_roll", "management"), ("post_roll", "management"),
    ("raise_funds", "management"), ("mortgage_takeover", "management"),
])
def test_phase_owner_and_offer_owner_call_order(phase, area):
    from types import SimpleNamespace
    calls = []
    owners = {a: Model("roll", calls, a) for a in ("buy", "auctions", "management", "offers", "responses")}
    player = hybrid().HybridPlayer(owners)
    observation = SimpleNamespace(decision=SimpleNamespace(kind=phase))
    choice = player.choose(observation, ())
    assert choice.explanation == area
    assert calls == ([(area, True)] if area in ("offers", "responses") else [("offers", True), (area, False)])


@pytest.mark.parametrize("kind", ["propose_trade", "counter_trade"])
def test_offer_proposal_preempts_other_owner_and_bot_proposal_uses_offer_owner(kind):
    from types import SimpleNamespace
    calls = []
    other = Player("buy", calls, "other")
    offerer = Player(kind, calls, "offerer")
    owners = dict.fromkeys(("buy", "auctions", "management", "responses"), other)
    owners["offers"] = offerer
    player = hybrid().HybridPlayer(owners)
    observation = SimpleNamespace(decision=SimpleNamespace(kind="buy_decision"))
    assert player.choose(observation, ()).action.kind == kind
    assert calls == ["offerer"]
    calls.clear()
    offerer.kind, other.kind = "decline", kind
    assert player.choose(observation, ()).action.kind == "decline"
    assert calls == ["offerer", "other"]
    calls.clear()
    owners["offers"] = other
    assert hybrid().HybridPlayer(owners).choose(observation, ()).action.kind == kind
    assert calls == ["other"]


@pytest.mark.parametrize("profile_id", [MINI, V7])
def test_real_model_cannot_start_trades_when_another_component_owns_offers(profile_id):
    from boardcraft.games.rentcraft.inference.bot import RentcraftInferenceBot
    profile, weights = policy(profile_id)
    arrays = dict(weights.weights)
    bias = arrays["pi.bias"].copy()
    # V7 opens an empty draft, adds cash and sends; mini uses a generated candidate.
    bias[178 if profile_id == V7 else 173] = 100
    bias[210] = 50
    bias[219] = 75
    arrays["pi.bias"] = bias
    player = RentcraftInferenceBot(profile, NumpyPolicy(profile, {**weights.architecture, "hidden":[2]}, arrays), 7)
    engine = fresh()
    seat = engine.decision().player
    observation, legal = engine.observe(seat), engine.legal_actions(seat)
    engine.game.state.owner[1] = seat
    engine.game.state.owner[3] = 1-seat
    observation, legal = engine.observe(seat), engine.legal_actions(seat)
    if profile_id == MINI:
        from boardcraft.games.rentcraft.state import Debt
        engine.game.state.phase = Phase.RAISE_FUNDS
        engine.game.state.players[seat].cash = 0
        engine.game.state.debts = [Debt(seat,1-seat,1000)]
        observation, legal = engine.observe(seat), engine.legal_actions(seat)
    unrestricted = RentcraftInferenceBot(profile, player.policy, 7)
    assert unrestricted.choose(observation, legal).action.kind == "propose_trade"
    before = engine.snapshot()
    assert player.choose(observation, legal, allow_trades=False).action.kind == ("mortgage" if profile_id == MINI else "roll")
    assert engine.snapshot() == before
    assert player.save_state()["notes"]["pending"] is None


def catalog_with_models(tmp_path):
    import hashlib
    import struct
    class StoredInfo(zipfile.ZipInfo):
        def FileHeader(self, zip64=None):
            name = self.filename.encode("ascii")
            return (struct.pack("<4s5H3I2H", b"PK\x03\x04", 45, 0, 0, 0, 33,
                                self.CRC, 0xFFFFFFFF, 0xFFFFFFFF, len(name), 20)
                    + name + struct.pack("<HHQQ", 1, 16, self.file_size, self.compress_size))
    sources = {}
    for alias, profile_id in [("mini", MINI), ("v7", V7)]:
        profile, weights = policy(profile_id)
        root = tmp_path / alias
        root.mkdir()
        path = root / "weights.npz"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, array in sorted(weights.weights.items()):
                info = StoredInfo(name + ".npy")
                info.create_version = info.extract_version = 45
                info.external_attr = 0o600 << 16
                with archive.open(info, "w", force_zip64=True) as stream:
                    np.lib.format.write_array(stream, array, allow_pickle=False)
        metadata = {**profile.identities(), "format": 1, "architecture": dict(weights.architecture),
                    "display_name": "Synthetic " + alias, "weights_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        meta = root / "metadata.json"
        meta.write_text(json.dumps(metadata), encoding="utf-8")
        sources[alias] = api.NumpySource(meta, path)
    return api.RuntimeBotCatalog(sources)


def test_catalog_pins_component_identities_options_and_alias_changes(tmp_path):
    catalog = catalog_with_models(tmp_path)
    selected = "hybrid:buy=miser:buy_reserve=200;offers=mini;rest=v7"
    reference = catalog.pin("rentcraft", selected)
    assert api.reference_from_data(api.reference_data(reference)) == reference
    assert api.validate_reference(reference, get_game("rentcraft")) == reference
    bot = catalog.resolve(reference, 7)
    assert bot.owners["buy"].save_state()["options"]["buy_reserve"] == 200
    assert bot.owners["offers"].profile.id == MINI
    assert bot.owners["management"].profile.id == V7
    same = catalog.pin("rentcraft", "hybrid:responses=v7;management=v7;auctions=v7;offers=mini;buy=miser:buy_reserve=200")
    assert same == reference
    catalog.register("mini", catalog._sources["v7"])
    assert catalog.pin("rentcraft", selected) != reference
    assert catalog.resolve(reference, 99).owners["offers"].profile.id == MINI
    with pytest.raises(ValueError):
        catalog.pin("dice_circuit", "hybrid:rest=miser")


def test_all_35_rules_resolve_as_hybrid_components():
    catalog = api.default_catalog()
    for descriptor in catalog.list_bots("rentcraft"):
        spec = "hybrid:rest=" + descriptor.choice_id
        reference = catalog.pin("rentcraft", spec)
        bot = catalog.resolve(reference, 7)
        assert all(owner is bot.owners["buy"] for owner in bot.owners.values())
        engine = fresh()
        seat = engine.decision().player
        choice = bot.choose(engine.observe(seat), engine.legal_actions(seat))
        engine.apply(seat, choice.action, engine.decision().id)


def test_hybrid_checkpoint_is_per_component_bound_and_atomic(tmp_path):
    catalog = catalog_with_models(tmp_path)
    reference = catalog.pin("rentcraft", "hybrid:offers=miser;buy=mini;rest=v7")
    player = catalog.resolve(reference, 7)
    engine = fresh()
    seat = engine.decision().player
    player.initialize(engine.observe(seat))
    player.choose(engine.observe(seat), engine.legal_actions(seat))
    saved = player.save_state()
    clone = catalog.resolve(reference, 99)
    clone.load_state(copy.deepcopy(saved))
    assert clone.save_state() == saved
    assert clone.choose(engine.observe(seat), engine.legal_actions(seat)) == player.choose(engine.observe(seat), engine.legal_actions(seat))
    for mutate in [lambda s: s.update(version=True), lambda s: s.update(extra=1),
                   lambda s: s.update(binding="0" * 64), lambda s: s["components"].pop(next(iter(s["components"]))),
                   lambda s: s["components"].update(unknown={}),
                   lambda s: s["components"].update({list(s["components"])[-1]: {}})]:
        damaged = copy.deepcopy(saved)
        mutate(damaged)
        before = clone.save_state()
        with pytest.raises(ValueError): clone.load_state(damaged)
        assert clone.save_state() == before
    other = catalog.resolve(catalog.pin("rentcraft", "hybrid:offers=miser;buy=v7;rest=mini"), 7)
    with pytest.raises(ValueError): other.load_state(saved)


def test_hybrid_table_restart_cas_tournament_and_mcp(tmp_path):
    from boardcraft.server.service import PlayService
    from boardcraft.play.tournaments import TournamentSpec, schedule, gameplay_runner
    from boardcraft.play.records import validate_record
    from boardcraft.mcp.server import create_server
    from mcp.client import Client
    catalog = catalog_with_models(tmp_path)
    spec = "hybrid:offers=miser;buy=mini;rest=v7"
    service = PlayService(tmp_path / "store", bot_catalog=catalog, seed_factory=lambda: 19)
    async def create():
        async with Client(create_server(service)) as client:
            response = await client.call_tool("create_game", {"config": {"game_id":"rentcraft", "variant":"base", "players":2}, "seats":[spec, spec]})
            assert not response.is_error, response
            return json.loads(response.content[0].text)
    made = asyncio.run(create())
    key = made["table_id"]
    expected = api.reference_data(catalog.pin("rentcraft", spec))
    assert all(s["bot_ref"] == expected for s in made["seats"])
    service.start_game(key, made["creator_credential"])
    state = service._read_state(key)
    assert all(s["bot_state"] is not None for s in state["seats"])
    seen = set()
    for _ in range(20):
        work, = service.bot_work(key)
        stale, = service.bot_work(key)
        choice = service.choose_bot(work)
        assert service.apply_bot(work, choice)
        committed = service._read_state(key)
        assert not service.apply_bot(stale, service.choose_bot(stale))
        assert service._read_state(key) == committed
        seen.add(work.seat)
        if len(seen) == 2: break
    assert seen == {0,1}
    restarted = PlayService(tmp_path / "store", bot_catalog=catalog)
    work, = restarted.bot_work(key)
    assert restarted.apply_bot(work, restarted.choose_bot(work))
    args = dict(name="Hybrid fixture", config=dict(game_id="rentcraft", variant="base", players=2, settings={"max_rounds":1}),
                rules_version="1", engine_version="1", seats=[spec,spec], seeds=[19], sampler_seeds=[[2,3]], rotations=[0],
                match_count=1, schedule_cap=1, max_inflight=1, max_record_bytes=1000000, max_decisions=100)
    created = service.create_tournament(args)
    try:
        tournament = TournamentSpec.from_data(service.get_tournament(created["tournament_id"])["spec"])
        assert all(api.reference_data(r) == expected for r in tournament.references)
        match, = schedule(tournament)
        record = gameplay_runner(tournament, catalog)(match)
        assert record.complete and validate_record(record) == record
        assert all(api.reference_data(p.bot_ref) == expected for p in record.participants)
    finally:
        service.close_tournaments()


def test_checkpoint_rejects_components_from_different_seats(tmp_path):
    catalog = catalog_with_models(tmp_path)
    reference = catalog.pin("rentcraft", "hybrid:buy=mini;rest=v7")
    engine = fresh()
    player, donor = catalog.resolve(reference,7), catalog.resolve(reference,7)
    player.initialize(engine.observe(0))
    donor.initialize(engine.observe(1))
    mixed = player.save_state()
    key = next(k for k,v in mixed["components"].items() if v["seat"] == 0)
    mixed["components"][key] = donor.save_state()["components"][key]
    before = player.save_state()
    clone = catalog.resolve(reference,7)
    with pytest.raises(ValueError): clone.load_state(mixed)
    assert player.save_state() == before


@pytest.mark.parametrize("spec", ["hybrid:rest=unknown", "hybrid:rest=miser:no_such_option=1",
                                      "hybrid:rest=miser:buy_reserve=true", "hybrid:rest=miser:buy_reserve=1,",
                                      "hybrid:rest=miser:buy_reserve=1,buy_reserve=2"])
def test_invalid_component_or_options_are_refused(spec):
    with pytest.raises(ValueError): api.default_catalog().pin("rentcraft",spec)


def test_hybrid_reference_refuses_nested_foreign_and_malformed_components():
    from boardcraft.contracts import RuleBotReference
    from boardcraft.inference.spec import digest
    catalog = api.default_catalog()
    reference = catalog.pin("rentcraft","hybrid:rest=miser")
    for mutate in [lambda d: d["owners"].pop("responses"),
                   lambda d: d["owners"].update(unknown=d["owners"]["buy"]),
                   lambda d: d["owners"].update(buy=api.reference_data(reference)),
                   lambda d: d["owners"].update(buy=api.reference_data(catalog.pin("dice_circuit","random")))]:
        options = json.loads(reference.options_json)
        mutate(options)
        changed = replace(reference,identity="",options_json=api._options_json(options))
        changed = replace(changed,identity=digest(api._payload(changed)))
        with pytest.raises(ValueError): api.reference_from_data(api._reference_data(changed))


@pytest.mark.parametrize("table_seed",[19,23])
def test_table_hybrid_initialization_preserves_each_seats_sampler_seed(tmp_path,table_seed):
    import hashlib
    from boardcraft.server.service import PlayService
    service = PlayService(tmp_path / "store",seed_factory=lambda:table_seed)
    reference = api.default_catalog().pin("rentcraft","hybrid:rest=random")
    made = service.create_game(GameConfig("rentcraft","base",2),["hybrid:rest=random"] * 2)
    service.start_game(made["table_id"],made["creator_credential"])
    state = service._read_state(made["table_id"])
    for seat in (0,1):
        seed = int.from_bytes(hashlib.sha256(f"{table_seed}:{seat}".encode()).digest()[:8],"big")
        expected = service.bot_catalog.resolve(reference,seed)
        engine = get_game("rentcraft").new(GameConfig("rentcraft","base",2),table_seed)
        expected.initialize(engine.observe(seat))
        assert state["seats"][seat]["bot_state"] == expected.save_state()


def test_abandoned_offer_notes_reach_the_next_model_component_and_restore(tmp_path,monkeypatch):
    catalog = catalog_with_models(tmp_path)
    reference = catalog.pin("rentcraft","hybrid:offers=mini;rest=v7")
    player = catalog.resolve(reference,7)
    engine = fresh()
    seat = engine.decision().player
    player.initialize(engine.observe(seat))
    script = iter([178,210,220,0])
    real_best = NumpyPolicy.best
    def best(policy,vector,mask):
        if policy.profile.id == MINI:
            index = next(script)
            assert mask[index]
            return index
        return real_best(policy,vector,mask)
    monkeypatch.setattr(NumpyPolicy,"best",best)
    assert player.choose(engine.observe(seat),engine.legal_actions(seat)).action.kind == "roll"
    states = player.save_state()["components"]
    assert all([row[1]["kind"] for row in state["notes"]["records"]] == ["abandoned_draft"] for state in states.values())
    restored = catalog.resolve(reference,999)
    restored.load_state(player.save_state())
    assert restored.save_state() == player.save_state()
    malformed = player.save_state()
    next(iter(malformed["components"].values()))["notes"]["records"].clear()
    before = restored.save_state()
    with pytest.raises(ValueError): restored.load_state(malformed)
    assert restored.save_state() == before
