"""Record identity is explicit, versioned and independent of current providers."""

import pytest
from boardcraft.contracts import GameConfig
from boardcraft.registry import get_game
from boardcraft.play import records
from boardcraft.play.runner import run_game
from boardcraft.play.replay import replay
from tests.inference.test_catalog import catalog_api
from tests.inference.test_catalog import artifact


def test_generic_choose_only_bot_defaults_external_and_honest_human_is_allowed(
    tmp_path,
):
    module = get_game("dice_circuit")
    config = GameConfig("dice_circuit", "base", 1)
    assert hasattr(records, "ParticipantRecord"), "Typed participants are missing"
    participant = records.ParticipantRecord(0, "human", display_name="Human adapter")
    generic = run_game(module, config, 0, [module.bot("random", 2)])
    assert generic.version == "2" and generic.participants == (
        records.ParticipantRecord(0, "external"),
    )
    declared = run_game(
        module, config, 0, [module.bot("random", 2)], participants=(participant,)
    )
    path = tmp_path / "record.gz"
    records.write_record(declared, path)
    assert records.read_record(path) == declared
    assert replay(declared).result() == generic.final_result
    assert declared.participants == (participant,)


def test_exact_builtin_identity_and_legacy_unknown_roundtrip(tmp_path):
    api = catalog_api()
    module, catalog = get_game("dice_circuit"), api.RuntimeBotCatalog()
    ref = catalog.pin("dice_circuit", "random")
    participant = records.ParticipantRecord(0, "builtin", ref, "Random bot")
    record = run_game(
        module,
        GameConfig("dice_circuit", "base", 1),
        0,
        [catalog.resolve(ref, 2)],
        participants=(participant,),
    )
    assert records.validate_record(record) == record
    data = records.record_data(record)
    data["version"] = "1"
    del data["participants"]
    legacy = records._decode(data)
    assert legacy.participants == (records.ParticipantRecord(0, "unknown"),)
    assert records.record_data(legacy) == data
    assert replay(legacy).result() == record.final_result


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "seat", "kind", "ref", "extra", "compatibility"]
)
def test_malformed_participants_reject(change):
    api = catalog_api()
    module, catalog = get_game("dice_circuit"), api.RuntimeBotCatalog()
    ref = catalog.pin("dice_circuit", "random")
    record = run_game(
        module,
        GameConfig("dice_circuit", "base", 1),
        0,
        [catalog.resolve(ref, 2)],
        participants=(records.ParticipantRecord(0, "builtin", ref, "Random bot"),),
    )
    data = records.record_data(record)
    if change == "missing":
        del data["participants"]
    elif change == "duplicate":
        data["participants"] *= 2
    elif change == "seat":
        data["participants"][0]["seat"] = True
    elif change == "kind":
        data["participants"][0]["kind"] = "human"
    elif change == "ref":
        data["participants"][0]["bot_ref"] = None
    elif change == "compatibility":
        data["participants"][0]["bot_ref"]["compatibility"]["rules_version"] = "999"
    else:
        data["participants"][0]["path"] = "/forbidden"
    with pytest.raises(ValueError):
        records._decode(data)


def test_model_participant_roundtrip_and_prefix_replay_need_no_model_files(tmp_path):
    api = catalog_api()
    paths = artifact(tmp_path / "artifact")
    catalog = api.RuntimeBotCatalog({"fixture": api.NumpySource(*paths)})
    reference = catalog.pin("dice_circuit", "fixture")
    module = get_game("dice_circuit")
    participant = records.ParticipantRecord(
        0, "model", reference, "Frozen synthetic policy"
    )
    record = run_game(
        module,
        GameConfig("dice_circuit", "base", 1),
        0,
        [catalog.resolve(reference, 7)],
        participants=(participant,),
    )
    destination = tmp_path / "record.gz"
    records.write_record(record, destination)
    for path in paths:
        path.unlink()
    restored = records.read_record(destination)
    assert restored == record
    assert restored.participants == (participant,)
    for prefix in (0, len(record.decisions) // 2, len(record.decisions)):
        assert replay(restored, prefix).snapshot() == replay(record, prefix).snapshot()
