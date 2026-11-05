"""Synthetic Rentcraft sources use the same pins in every public entry point."""
import json
from dataclasses import replace

import pytest
from boardcraft.registry import get_game
from boardcraft.inference import catalog as api
from boardcraft.games.rentcraft.inference.profiles import MINI, V7
from test_runtime import rent_artifact


@pytest.mark.parametrize("profile_id", [MINI, V7])
def test_catalog_loads_local_profile_and_canonical_greedy_pin(tmp_path, profile_id):
    meta, weights, _, _ = rent_artifact(tmp_path, profile_id=profile_id)
    catalog = api.RuntimeBotCatalog({"synthetic": api.NumpySource(meta, weights)})
    reference = catalog.pin("rentcraft", "synthetic")
    assert reference.sampling_version == "rentcraft-greedy-v1"
    assert json.loads(reference.architecture_json)["selection"] == "greedy"
    assert api.reference_from_data(api.reference_data(reference)) == reference
    assert api.validate_reference(reference, get_game("rentcraft")) == reference
    bot = catalog.resolve(reference, 7)
    assert bot.profile.id == profile_id
    assert bot.policy.profile == bot.profile
    foreign = replace(reference, compatibility=replace(reference.compatibility, encoder_id=V7 if profile_id == MINI else MINI))
    with pytest.raises(api.BotIncompatible):
        catalog.resolve(foreign, 7)


def test_alias_update_keeps_old_model_pinned(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir(); second.mkdir()
    a = rent_artifact(first)
    b = rent_artifact(second, profile_id=V7)
    catalog = api.RuntimeBotCatalog({"synthetic": api.NumpySource(*a[:2])})
    old = catalog.pin("rentcraft", "synthetic")
    catalog.register("synthetic", api.NumpySource(*b[:2]))
    new = catalog.pin("rentcraft", "synthetic")
    assert new != old
    assert catalog.resolve(old, 1).profile.id == MINI
    assert catalog.resolve(new, 1).profile.id == V7


def test_neural_capability_requires_both_local_profiles(monkeypatch):
    module = get_game("rentcraft")
    assert module.info().capabilities.neural_inference
    original = module.inference_profile
    def missing(profile_id, version):
        if profile_id == V7:
            raise ValueError("Unavailable profile")
        return original(profile_id, version)
    monkeypatch.setattr(module, "inference_profile", missing)
    assert not module.info().capabilities.neural_inference


def test_bundled_directory_is_automatic_and_uses_strict_sources(tmp_path, monkeypatch):
    bundled = tmp_path / "models"
    bundled.mkdir()
    meta, weights, _, _ = rent_artifact(bundled)
    manifest = {"version": 1, "models": [{"choice_id": "bundled-fixture", "metadata": meta.name, "weights": weights.name}]}
    (bundled / "models.json").write_text(json.dumps(manifest), encoding="utf-8")
    original = api.files
    monkeypatch.setattr(api, "files", lambda package: bundled if package == "boardcraft.models" else original(package))
    reference = api.default_catalog().pin("rentcraft", "bundled-fixture")
    assert reference.compatibility.encoder_id == MINI
    own = tmp_path / "own.json"
    own.write_text(json.dumps({"version": 1, "models": [{"choice_id": "own-fixture", "metadata": str(meta), "weights": str(weights)}]}))
    combined = api.default_catalog(own)
    assert combined.pin("rentcraft", "bundled-fixture") == combined.pin("rentcraft", "own-fixture")
    damaged = json.loads(meta.read_text())
    damaged["encoder_layout_hash"] = "0" * 64
    meta.write_text(json.dumps(damaged))
    assert all(d.kind == "builtin" for d in api.default_catalog().list_bots("rentcraft"))


def test_bundled_and_own_alias_collision_is_explicit(tmp_path, monkeypatch):
    bundled = tmp_path / "models"
    bundled.mkdir()
    entry = {"choice_id": "same", "metadata": "metadata.json", "weights": "weights.npz"}
    manifest = json.dumps({"version": 1, "models": [entry]})
    (bundled / "models.json").write_text(manifest)
    own = tmp_path / "own.json"
    own.write_text(manifest)
    original = api.files
    monkeypatch.setattr(api, "files", lambda package: bundled if package == "boardcraft.models" else original(package))
    with pytest.raises(ValueError):
        api.default_catalog(own)


def make_service(tmp_path, *, profile_id=MINI):
    from boardcraft.server.service import PlayService
    artifact = tmp_path / "artifact"
    artifact.mkdir(parents=True)
    paths = rent_artifact(artifact, profile_id=profile_id)
    catalog = api.RuntimeBotCatalog({"synthetic": api.NumpySource(*paths[:2])})
    return PlayService(tmp_path / "store", bot_catalog=catalog, seed_factory=lambda: 19), catalog


def test_table_model_checkpoints_are_initialized_before_any_seat_moves(tmp_path):
    from boardcraft.contracts import GameConfig
    service, catalog = make_service(tmp_path)
    made = service.create_game(GameConfig("rentcraft", "base", 2), ["synthetic"] * 2)
    key = made["table_id"]
    reference = api.reference_data(catalog.pin("rentcraft", "synthetic"))
    assert all(s["bot_ref"] == reference for s in made["seats"])
    service.start_game(key, made["creator_credential"])
    state = service._read_state(key)
    assert [s["bot_state"]["seat"] for s in state["seats"]] == [0, 1]
    seen = set()
    for _ in range(20):
        work, = service.bot_work(key)
        stale, = service.bot_work(key)
        choice = service.choose_bot(work)
        duplicate = service.choose_bot(stale)
        assert choice == duplicate
        assert service.apply_bot(work, choice)
        committed = service._read_state(key)
        assert not service.apply_bot(stale, duplicate)
        assert service._read_state(key) == committed
        seen.add(work.seat)
        if len(seen) == 2: break
    assert seen == {0, 1}
    from boardcraft.server.service import PlayService
    service = PlayService(tmp_path / "store", bot_catalog=catalog)
    work, = service.bot_work(key)
    assert service.apply_bot(work, service.choose_bot(work))
    text = json.dumps(service.get_view(key))
    assert all(secret not in text for secret in ('"bot_state"', '"abandoned_draft"', '"trade_draft"', '"trade_draft_opens"', '"binding"'))


def test_lobby_tournament_schedule_and_runner_keep_same_model_pin(tmp_path):
    from boardcraft.contracts import GameConfig
    from boardcraft.play.tournaments import TournamentSpec, schedule, gameplay_runner
    from boardcraft.play.records import record_data, validate_record
    service, catalog = make_service(tmp_path)
    config = GameConfig("rentcraft", "base", 2, {"max_rounds": 1})
    made = service.create_game(config, ["synthetic"] * 2)
    expected = made["seats"][0]["bot_ref"]
    args = dict(name="Synthetic fixture", config=dict(game_id="rentcraft", variant="base", players=2, settings={"max_rounds": 1}),
                rules_version="1", engine_version="1", seats=["synthetic"] * 2, seeds=[19],
                sampler_seeds=[[2, 3]], rotations=[0], match_count=1, schedule_cap=1,
                max_inflight=1, max_record_bytes=1000000, max_decisions=100)
    created = service.create_tournament(args)
    try:
        spec = TournamentSpec.from_data(service.get_tournament(created["tournament_id"])["spec"])
        assert all(api.reference_data(r) == expected for r in spec.references)
        match, = schedule(spec)
        record = gameplay_runner(spec, catalog)(match)
        assert record.complete and validate_record(record) == record
        assert all(api.reference_data(p.bot_ref) == expected for p in record.participants)
        # Model decisions carry their reasoning for the replay: leading choices with probabilities (no other fields).
        notes = [op["meta"] for op in record_data(record)["operations"] if op.get("meta")]
        assert notes and all(set(note) <= {"probabilities", "value"} and note["probabilities"] for note in notes)
    finally:
        service.close_tournaments()


def test_table_persists_explicit_meta_without_exposing_it(tmp_path):
    from boardcraft.contracts import GameConfig
    service, _ = make_service(tmp_path)
    made = service.create_game(GameConfig("rentcraft", "base", 2), ["synthetic"] * 2)
    key = made["table_id"]
    service.start_game(key, made["creator_credential"])
    work, = service.bot_work(key)
    choice = replace(service.choose_bot(work), meta={"value": 0.25})
    assert service.apply_bot(work, choice)
    with service.store.transaction() as db:
        operation = json.loads(db.execute("SELECT payload FROM steps WHERE table_id=?", (key,)).fetchone()[0])
    assert operation["meta"] == {"value": 0.25}
    assert "meta" not in service.get_view(key)


def test_cli_model_source_uses_rent_catalog_without_new_flags(tmp_path, monkeypatch):
    import sys
    import uvicorn
    from boardcraft import cli
    service, _ = make_service(tmp_path)
    manifest = tmp_path / "sources.json"
    manifest.write_text(json.dumps({"version": 1, "models": [{"choice_id": "synthetic", "metadata": str(tmp_path / "artifact" / "metadata.json"),
                                                            "weights": str(tmp_path / "artifact" / "weights.npz")}]}))
    captured = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: captured.append(app))
    monkeypatch.setattr(sys, "argv", ["boardcraft", "serve", "--data-dir", str(tmp_path / "cli-store"), "--model-source", str(manifest)])
    cli.main()
    assert captured
    from fastapi.testclient import TestClient
    with TestClient(captured[0]) as client:
        listed = client.get("/api/games/rentcraft/bots")
        assert listed.status_code == 200
        assert "synthetic" in listed.text
