"""Only synthetic, temporary policies enter the ordinary public catalog."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import importlib.util
import io
import json
import struct
import subprocess
import sys
import zlib
import numpy as np
import pytest
from boardcraft.registry import get_game


def catalog_api():
    assert importlib.util.find_spec("boardcraft.inference.catalog"), (
        "Runtime bot catalog is missing"
    )
    from boardcraft.inference import catalog

    return catalog


def test_rentcraft_rule_pins_options_and_resolution_survive_neural_capability(monkeypatch):
    api = catalog_api()
    module = get_game("rentcraft")
    catalog = api.RuntimeBotCatalog()
    before = catalog.list_bots("rentcraft")
    configured = catalog.pin_builtin("rentcraft", "miser", {"buy_reserve": 123})
    original_info = module.info

    def neural_info():
        info = original_info()
        return replace(info, capabilities=replace(info.capabilities, neural_inference=True))

    monkeypatch.setattr(module, "info", neural_info)
    assert catalog.list_bots("rentcraft") == before
    assert catalog.pin_builtin("rentcraft", "miser", {"buy_reserve": 123}) == configured
    for ref in [d.reference for d in before] + [configured]:
        assert api.validate_reference(ref, module) == ref
        assert catalog.resolve(ref, 11)


def test_dice_keeps_rejecting_rule_references_without_tensor_layout_binding():
    api = catalog_api()
    from boardcraft.inference.spec import digest

    data = {"kind": "builtin", "compatibility": {"game_id": "dice_circuit", "rules_version": "1",
            "observation_version": "1", "action_version": "1"}, "builtin_kind": "random",
            "behavior_version": "1", "options_json": "{}"}
    reference = api.reference_from_data({**data, "identity": digest({"domain": "boardcraft-rule-reference-v1", **data})})
    with pytest.raises(api.BotIncompatible):
        api.validate_reference(reference, get_game("dice_circuit"))
    with pytest.raises(api.BotIncompatible):
        api.RuntimeBotCatalog().resolve(reference, 1)


def artifact(root, bias=0, name="Synthetic policy", *, reverse=False, hidden=()):
    from boardcraft.inference.spec import identities, weight_shapes

    enc = get_game("dice_circuit").encoder()
    root.mkdir(parents=True, exist_ok=True)
    weights = root / "weights.npz"
    # Emit the canonical stored envelope even with older stdlib ZIP writers.
    members, directory = bytearray(), bytearray()
    network = {"family": "mlp", "hidden": list(hidden), "activation": "relu"}
    arrays = {
        key: np.zeros(shape, np.float32)
        for key, shape in weight_shapes(enc, network).items()
    }
    arrays["pi.bias"][:] = bias
    if hidden:
        arrays["trunk.layers.0.bias"][:] = -1
        arrays["pi.weight"][:] = 1
    for key in sorted(arrays, reverse=reverse):
        array = arrays[key]
        stream = io.BytesIO()
        np.lib.format.write_array(stream, array, allow_pickle=False)
        data, member_name = stream.getvalue(), (key + ".npy").encode("ascii")
        crc, size, offset = zlib.crc32(data), len(data), len(members)
        members.extend(
            struct.pack(
                "<4s5H3I2H",
                b"PK\x03\x04",
                45,
                0,
                0,
                0,
                33,
                crc,
                0xFFFFFFFF,
                0xFFFFFFFF,
                len(member_name),
                20,
            )
        )
        members.extend(member_name + struct.pack("<HHQQ", 1, 16, size, size) + data)
        directory.extend(
            struct.pack(
                "<4s6H3I5H2I",
                b"PK\x01\x02",
                45,
                45,
                0,
                0,
                0,
                33,
                crc,
                size,
                size,
                len(member_name),
                0,
                0,
                0,
                0,
                0o600 << 16,
                offset,
            )
            + member_name
        )
    end = struct.pack(
        "<4s4H2IH",
        b"PK\x05\x06",
        0,
        0,
        len(arrays),
        len(arrays),
        len(directory),
        len(members),
        0,
    )
    weights.write_bytes(members + directory + end)
    metadata = dict(
        identities(enc),
        format=1,
        architecture=network,
        display_name=name,
        weights_sha256=hashlib.sha256(weights.read_bytes()).hexdigest(),
    )
    path = root / "metadata.json"
    path.write_text(json.dumps(metadata), encoding="utf-8")
    return path, weights


def test_empty_catalog_truthful_builtin_source_and_reference_validation():
    api = catalog_api()
    catalog = api.RuntimeBotCatalog()
    entries = catalog.list_bots("dice_circuit")
    module = get_game("dice_circuit")
    assert tuple(e.choice_id for e in entries) == module.info().bot_kinds
    assert tuple(e.display_name for e in entries) == ("Random bot", "Greedy bot", "Strategist")
    for entry in entries:
        assert catalog.pin("dice_circuit", entry.choice_id) == entry.reference
        assert (
            api.reference_from_data(api.reference_data(entry.reference))
            == entry.reference
        )
    with pytest.raises(ValueError):
        catalog.resolve(replace(entries[0].reference, behavior_version="999"), 0)


def test_legacy_reference_identity(tmp_path):
    api = catalog_api()
    catalog = api.RuntimeBotCatalog({"fixture": api.NumpySource(*artifact(tmp_path))})
    expected = {
        "random": "8a0348d7d51c470803e732ed3942bbc4ec595fa159e32853d0bf1d7356ddc467",
        "greedy": "9b669f791536fdbca349db4a27b2f1d8f269cff1ccd3b08936a7275d7c6296c0",
        "fixture": "1d18cbf0638c2aca6b0128bebf7e1a4336702cf471c27267f9c84532bf8c546c",
    }
    compatibility = {
        "game_id": "dice_circuit", "rules_version": "1", "observation_version": "1",
        "encoder_id": "dice-circuit-visible", "encoder_version": "1",
        "encoder_layout_hash": "61f19c7616f233ed70c1503bbd7a4f5f226179e53aa0e3eec0190c1d3b3bd010",
        "action_id": "dice-circuit-actions", "action_version": "1",
        "action_layout_hash": "c4d8fd08a5ed20446d01687a6c4bd384d4d494c9d1123f1431eb0fe7061c1b39",
        "input_size": 3697, "output_size": 301,
    }
    for name, identity in expected.items():
        ref = catalog.pin("dice_circuit", name)
        if name == "fixture":
            assert ref.sampling_version == api.GREEDY_SAMPLING_VERSION
            ref = api.with_model_selection(ref, api.SAMPLING_VERSION)
        data = api.reference_data(ref)
        assert ref.identity == identity
        assert data == {
            "kind": "model" if name == "fixture" else "builtin",
            "identity": identity, "compatibility": compatibility,
            "policy_sha256": "7b56ca0f42bf55b21ded833992a911d2d97c8b318c003d86e111d8c00f2dfc7a" if name == "fixture" else None,
            "builtin_kind": None if name == "fixture" else name,
            "behavior_version": None if name == "fixture" else "1",
            "inference_format": 1 if name == "fixture" else None,
            "architecture_json": '{"activation":"relu","family":"mlp","hidden":[]}' if name == "fixture" else None,
            "sampling_version": "numpy-choice-v1" if name == "fixture" else None,
        }
        assert api.reference_from_data(data) == ref


@pytest.fixture
def encoderless_catalog(monkeypatch):
    from boardcraft import contracts
    api = catalog_api()

    class ConfiguredBot:
        def __init__(self, options):
            self.options = options

        def choose(self, observation, legal_actions):
            return contracts.BotChoice(legal_actions[self.options.get("index", 0)], "Choose configured action.")

    class PlayOnlyModule:
        game_id = "play_only"
        rules_version = "2"
        engine_version = "3"
        observation_version = "4"
        action_version = "5"

        def info(self):
            return contracts.GameInfo(self.game_id, self.rules_version, self.engine_version,
                                      "Play only", ("base",), 2, 4, ("rule",),
                                      contracts.PlayCapabilities(False, True, True, True))

        def encoder(self):
            raise AssertionError("Play-only capability must be checked before encoding")

        def bot_info(self):
            return (contracts.BuiltinBotInfo("rule", "Rule+", "6"),)

        def bot_settings_schema(self, kind):
            if kind != "rule":
                raise ValueError("Unknown bot kind")
            return {"type": "object", "additionalProperties": False,
                    "properties": {"index": {"type": "integer", "minimum": 0, "maximum": 1, "default": 0}}}

        def bot(self, kind, seed, *, options=None):
            from boardcraft.settings import normalize_settings
            return ConfiguredBot(normalize_settings(self.bot_settings_schema(kind), {} if options is None else options))

    module = PlayOnlyModule()
    original_get = api.get_game
    monkeypatch.setattr(api, "get_game", lambda game_id: module if game_id == module.game_id else original_get(game_id))
    monkeypatch.setattr(api, "list_games", lambda: (module.info(), get_game("dice_circuit").info()))
    return api, module


def test_encoderless_builtin_roundtrip(encoderless_catalog, tmp_path, monkeypatch):
    from boardcraft import contracts
    assert hasattr(contracts, "PlayCapabilities"), "Encoderless play capabilities are missing"
    api, module = encoderless_catalog

    def forbidden_load(*args):
        raise AssertionError("Unsupported model must be rejected before reading files")

    monkeypatch.setattr(api, "load_artifact", forbidden_load)
    catalog = api.RuntimeBotCatalog({"fixture": api.NumpySource(tmp_path / "missing.json", tmp_path / "missing.npz")})
    entries = catalog.list_bots(module.game_id)
    assert len(entries) == 1 and entries[0].display_name == "Rule+"
    default = catalog.pin(module.game_id, "rule")
    assert default == catalog.pin_builtin(module.game_id, "rule", {"index": 0})
    assert default.options_json == "{}"
    ref = catalog.pin_builtin(module.game_id, "rule", {"index": 1})
    assert isinstance(ref, contracts.BotReference) and isinstance(ref, contracts.RuleBotReference)
    assert len({ref, default}) == 2 and ref.identity != default.identity
    assert ref.compatibility == contracts.RuleCompatibility("play_only", "2", "4", "5")
    data = api.reference_data(ref)
    assert set(data) == {"kind", "identity", "compatibility", "builtin_kind", "behavior_version", "options_json"}
    assert data["compatibility"] == {"game_id": "play_only", "rules_version": "2", "observation_version": "4", "action_version": "5"}
    assert data["options_json"] == '{"index":1}'
    restored = api.reference_from_data(json.loads(json.dumps(data)))
    assert restored == ref and api.validate_reference(restored, module) == ref
    actions = tuple(contracts.Action(str(i), "select", {}) for i in range(2))
    assert catalog.resolve(restored, 2).choose(None, actions).action == actions[1]
    assert catalog.resolve(default, 2).choose(None, actions).action == actions[0]
    with pytest.raises(ValueError):
        catalog.pin(module.game_id, "fixture")
    with pytest.raises(ValueError):
        catalog.pin_builtin(module.game_id, "rule", {"index": True})
    with pytest.raises(ValueError):
        catalog.pin_builtin(module.game_id, "rule", {"extra": 1})
    for invalid in (None, [], "options"):
        with pytest.raises(ValueError):
            catalog.pin_builtin(module.game_id, "rule", invalid)
    from boardcraft.inference.spec import identities
    compat = contracts.BotCompatibility(**{**identities(get_game("dice_circuit").encoder()), "game_id": module.game_id})
    unsupported_model = api.policy_reference(compat, {"family": "mlp", "hidden": [], "activation": "relu"}, "0" * 64)
    with pytest.raises(api.BotIncompatible):
        catalog.resolve(unsupported_model, 2)
    for changes in ({"policy_sha256": "0" * 64}, {"inference_format": 1},
                    {"architecture_json": "{}"}, {"sampling_version": "sampling-v1"}):
        with pytest.raises(ValueError):
            api.reference_data(replace(ref, **changes))
    with pytest.raises(api.BotIncompatible):
        catalog.resolve(replace(ref, compatibility=contracts.RuleCompatibility("play_only", "999", "4", "5")), 2)


def test_rule_reference_shape_and_options_are_strict(encoderless_catalog):
    from boardcraft import contracts
    assert hasattr(contracts, "PlayCapabilities"), "Encoderless play capabilities are missing"
    api, module = encoderless_catalog
    from boardcraft.inference.spec import digest
    catalog = api.RuntimeBotCatalog()
    ref = catalog.pin_builtin(module.game_id, "rule", {"index": 1})
    for key, value in (("policy_sha256", None), ("kind", "model"), ("options_json", "{\"index\": 1}"),
                       ("options_json", "[]"), ("options_json", '{"index":NaN}'),
                       ("options_json", '{"index":1,"index":0}')):
        data = api.reference_data(ref)
        data[key] = value
        payload = {k: v for k, v in data.items() if k != "identity"}
        data["identity"] = digest({"domain": "boardcraft-rule-reference-v1", **payload})
        with pytest.raises(ValueError):
            api.reference_from_data(data)
    for options in ('{"index":0}', '{"index":true}', '{"extra":1}'):
        data = api.reference_data(ref)
        data["options_json"] = options
        data["identity"] = digest({"domain": "boardcraft-rule-reference-v1", **{k: v for k, v in data.items() if k != "identity"}})
        parsed = api.reference_from_data(data)
        with pytest.raises(api.BotIncompatible):
            api.validate_reference(parsed, module)


def test_display_names_allow_plus_but_machine_ids_do_not():
    api = catalog_api()
    assert api.display_name("Rule+") == "Rule+"
    with pytest.raises(ValueError):
        api.choice_id("rule+")


def test_default_builtin_options_keep_legacy_factory_call(monkeypatch):
    api = catalog_api()
    module = get_game("dice_circuit")
    original = module.bot

    def legacy_factory(kind, seed):
        return original(kind, seed)

    monkeypatch.setattr(module, "bot", legacy_factory)
    catalog = api.RuntimeBotCatalog()
    ref = catalog.pin_builtin("dice_circuit", "random", {})
    assert ref == catalog.pin("dice_circuit", "random")
    assert catalog.resolve(ref, 1)


def test_legacy_model_reference_requires_all_tensor_fields(tmp_path):
    api = catalog_api()
    ref = api.RuntimeBotCatalog({"fixture": api.NumpySource(*artifact(tmp_path))}).pin("dice_circuit", "fixture")
    for key in api.reference_data(ref):
        data = api.reference_data(ref)
        del data[key]
        with pytest.raises(ValueError):
            api.reference_from_data(data)
    for key in api.reference_data(ref)["compatibility"]:
        data = api.reference_data(ref)
        del data["compatibility"][key]
        with pytest.raises(ValueError):
            api.reference_from_data(data)


def test_loaded_envelope_single_validation_immutable_metadata(tmp_path, monkeypatch):
    from boardcraft.inference import model

    assert hasattr(model, "load_artifact"), "Validated policy envelope is missing"
    metadata, weights = artifact(tmp_path)
    original, calls = model.read_stored_arrays, []

    def read(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(model, "read_stored_arrays", read)
    loaded = model.load_artifact(metadata, weights, get_game("dice_circuit").encoder())
    assert len(calls) == 1
    with pytest.raises(TypeError):
        loaded.metadata["architecture"]["hidden"] += (7,)
    assert (
        loaded.metadata["weights_sha256"]
        == hashlib.sha256(weights.read_bytes()).hexdigest()
    )
    model.load_model(metadata, weights, get_game("dice_circuit").encoder())
    assert len(calls) == 2


def test_alias_rebind_cached_identity_restart_and_no_substitution(tmp_path):
    api = catalog_api()
    a, b = (
        artifact(tmp_path / "a"),
        artifact(tmp_path / "b", 1, "Second synthetic policy"),
    )
    catalog = api.RuntimeBotCatalog({"fixture": api.NumpySource(*a)})
    reference = catalog.pin("dice_circuit", "fixture")
    catalog.register("fixture", api.NumpySource(*b))
    assert catalog.pin("dice_circuit", "fixture") != reference
    assert catalog.resolve(reference, 7) is not catalog.resolve(reference, 7)
    a[1].write_bytes(b"replaced")
    assert catalog.resolve(reference, 7)
    restarted = api.RuntimeBotCatalog({"fixture": api.NumpySource(*b)})
    with pytest.raises(api.BotUnavailable) as error:
        restarted.resolve(reference, 7)
    assert error.value.reference == reference
    assert "replaced" not in str(error.value) and str(tmp_path) not in str(error.value)


def test_concurrent_initial_load_once_and_independent_seeded_rng(tmp_path, monkeypatch):
    api = catalog_api()
    paths = artifact(tmp_path)
    original, calls = api.load_artifact, []

    def load(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(api, "load_artifact", load)
    catalog = api.RuntimeBotCatalog({"fixture": api.NumpySource(*paths)})
    with ThreadPoolExecutor(4) as pool:
        refs = list(
            pool.map(lambda _: catalog.pin("dice_circuit", "fixture"), range(8))
        )
    assert len(calls) == 1 and len(set(refs)) == 1
    from boardcraft.contracts import GameConfig

    engine = get_game("dice_circuit").new(GameConfig("dice_circuit", "base", 1), 0)
    obs, actions = engine.observe(0), engine.legal_actions(0)
    first, second = catalog.resolve(refs[0], 8), catalog.resolve(refs[0], 8)
    assert [first.choose(obs, actions).action for _ in range(8)] == [
        second.choose(obs, actions).action for _ in range(8)
    ]


@pytest.mark.parametrize("choice", ["human", "greedy", "", "../path", "x" * 129])
def test_model_alias_collisions_and_unsafe_ids_reject(tmp_path, choice):
    api = catalog_api()
    with pytest.raises(ValueError):
        api.RuntimeBotCatalog({choice: api.NumpySource(*artifact(tmp_path))})


def test_reference_binds_architecture_and_rejects_forgery(tmp_path):
    api = catalog_api()
    paths = artifact(tmp_path)
    catalog = api.RuntimeBotCatalog({"fixture": api.NumpySource(*paths)})
    a = catalog.pin("dice_circuit", "fixture")
    metadata = json.loads(paths[0].read_text())
    assert (
        api.policy_reference(a.compatibility, metadata["architecture"], a.policy_sha256, sampling_version=a.sampling_version)
        == a
    )
    with pytest.raises(ValueError):
        api.policy_reference(a.compatibility, metadata["architecture"], "invalid")
    metadata["architecture"]["activation"] = "silu"
    paths[0].write_text(json.dumps(metadata))
    b = api.RuntimeBotCatalog({"fixture": api.NumpySource(*paths)}).pin(
        "dice_circuit", "fixture"
    )
    assert a.identity != b.identity
    data = api.reference_data(a)
    data["policy_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        api.reference_from_data(data)


def test_manifest_explicit_source_and_no_packaged_models(tmp_path):
    api = catalog_api()
    paths = artifact(tmp_path / "artifact")
    manifest = tmp_path / "sources.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "models": [
                    {
                        "choice_id": "fixture",
                        "metadata": str(paths[0]),
                        "weights": str(paths[1]),
                    }
                ],
            }
        )
    )
    assert len(api.default_catalog(manifest).list_bots("dice_circuit")) == 4
    assert len(api.default_catalog().list_bots("dice_circuit")) == 3


def test_aliases_share_one_loaded_policy_and_failed_sources_have_backoff(
    tmp_path, monkeypatch
):
    api = catalog_api()
    paths = artifact(tmp_path)
    original, calls = api.load_artifact, []

    def load(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(api, "load_artifact", load)
    source = api.NumpySource(*paths)
    catalog = api.RuntimeBotCatalog({"first": source, "second": source})
    assert catalog.pin("dice_circuit", "first") == catalog.pin("dice_circuit", "second")
    assert len(calls) == 1
    failed = api.RuntimeBotCatalog(
        {"first": api.NumpySource(tmp_path / "missing.json", tmp_path / "missing.npz")}
    )
    for _ in range(8):
        failed.list_bots("dice_circuit")
    assert len(calls) == 2


@pytest.mark.parametrize(
    "field",
    [
        "game_id",
        "rules_version",
        "observation_version",
        "encoder_id",
        "encoder_version",
        "encoder_layout_hash",
        "action_id",
        "action_version",
        "action_layout_hash",
        "input_size",
        "output_size",
    ],
)
def test_incompatible_metadata_cannot_be_discovered_or_pinned(tmp_path, field):
    api = catalog_api()
    paths = artifact(tmp_path)
    data = json.loads(paths[0].read_text())
    data[field] = (
        data[field] + 1
        if type(data[field]) is int
        else ("0" * 64 if field.endswith("_hash") else "incompatible")
    )
    paths[0].write_text(json.dumps(data))
    catalog = api.RuntimeBotCatalog({"fixture": api.NumpySource(*paths)})
    assert all(item.kind == "builtin" for item in catalog.list_bots("dice_circuit"))
    with pytest.raises(ValueError):
        catalog.pin("dice_circuit", "fixture")


def test_core_catalog_import_requires_no_optional_server_or_training_package():
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            "import sys; from boardcraft.inference.catalog import RuntimeBotCatalog; "
            "c=RuntimeBotCatalog(); r=c.pin('dice_circuit','random'); c.resolve(r,1); "
            "assert not any(k.split('.')[0] in {'torch','pydantic','fastapi','mcp','boardcraft_training'} for k in sys.modules)",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_policy_identity_is_independent_of_valid_archive_member_order(tmp_path):
    api = catalog_api()
    first = artifact(tmp_path / "first")
    second = artifact(tmp_path / "second", reverse=True)
    assert first[1].read_bytes() != second[1].read_bytes()
    a = api.RuntimeBotCatalog({"fixture": api.NumpySource(*first)}).pin(
        "dice_circuit", "fixture"
    )
    b = api.RuntimeBotCatalog({"fixture": api.NumpySource(*second)}).pin(
        "dice_circuit", "fixture"
    )
    assert a == b, "Archive packaging changed the frozen policy identity"
    assert a.policy_sha256 and not hasattr(a, "weights_sha256")


def test_canonical_policy_tensor_digest_framing_order_endian_and_changes():
    assert importlib.util.find_spec("boardcraft.inference.identity"), (
        "Canonical policy tensor digest is missing"
    )
    from boardcraft.inference.identity import canonical_tensor_digest

    base = np.arange(6, dtype=np.float32).reshape(2, 3)
    other = np.ones(2, dtype=np.float32)
    expected = canonical_tensor_digest({"a": base, "b": other})
    assert expected == canonical_tensor_digest(
        {"b": other, "a": np.asfortranarray(base)}
    )
    assert expected == canonical_tensor_digest({"a": base.astype(">f4"), "b": other})
    assert expected != canonical_tensor_digest({"a": base.reshape(3, 2), "b": other})
    assert expected != canonical_tensor_digest({"a": base + 1, "b": other})
    assert expected != canonical_tensor_digest({"ab": base, "b": other})
    with pytest.raises(ValueError):
        canonical_tensor_digest({"a": base.astype(np.float64)})
    with pytest.raises(ValueError):
        canonical_tensor_digest({"a": np.array([np.nan], np.float32)})


@pytest.mark.parametrize(
    "weights",
    [
        {"a": np.zeros((0, 3), np.float32)},
        {"a": np.array(1, np.float32)},
        {"../a": np.ones(3, np.float32)},
        {"a": [1.0]},
        {1: np.ones(1, np.float32)},
    ],
)
def test_canonical_policy_digest_rejects_unsupported_name_shape_and_objects(weights):
    from boardcraft.inference.identity import canonical_tensor_digest

    with pytest.raises(ValueError):
        canonical_tensor_digest(weights)


@pytest.mark.parametrize(
    "mutation",
    [
        "writeable",
        "mapping",
        "activation",
        "nested-hidden",
        "state-weights",
        "state-architecture",
        "state-size",
        "state-layout",
    ],
)
def test_loaded_policy_rejects_ordinary_mutation_and_keeps_exact_digest(
    tmp_path, mutation
):
    from boardcraft.inference.model import load_artifact
    from boardcraft.inference.identity import canonical_tensor_digest

    api = catalog_api()
    paths = artifact(tmp_path, hidden=(1,))
    loaded = load_artifact(*paths, get_game("dice_circuit").encoder())
    reference = api.model_reference(loaded)
    before = loaded.policy.logits(np.zeros(3697, np.float32))
    with pytest.raises((TypeError, AttributeError, ValueError)):
        if mutation == "writeable":
            loaded.policy._weights["pi.bias"].flags.writeable = True
        elif mutation == "mapping":
            loaded.policy._weights["pi.bias"] = np.full(301, 23, np.float32)
        elif mutation == "activation":
            loaded.policy._architecture["activation"] = "silu"
        elif mutation == "nested-hidden":
            loaded.policy._architecture["hidden"][0] = 7
        else:
            key, value = {
                "state-weights": ("_weights", {}),
                "state-architecture": ("_architecture", {}),
                "state-size": ("_input_size", 1),
                "state-layout": ("_layout", None),
            }[mutation]
            setattr(loaded.policy, key, value)
    np.testing.assert_array_equal(
        loaded.policy.logits(np.zeros(3697, np.float32)), before
    )
    assert canonical_tensor_digest(loaded.policy._weights) == loaded.policy_sha256
    assert api.model_reference(loaded) == reference


def test_cached_resolved_policy_is_immutable_including_array_view_metadata(tmp_path):
    api = catalog_api()
    catalog = api.RuntimeBotCatalog(
        {"fixture": api.NumpySource(*artifact(tmp_path, hidden=(1,)))}
    )
    reference = catalog.pin("dice_circuit", "fixture")
    first, second = catalog.resolve(reference, 3), catalog.resolve(reference, 3)
    vector = np.zeros(3697, np.float32)
    before = second.policy.logits(vector)
    view = first.policy._weights["pi.bias"]
    view.shape = (301, 1)
    view.dtype = np.uint8
    assert second.policy._weights["pi.bias"].shape == (301,)
    assert second.policy._weights["pi.bias"].dtype == np.float32
    with pytest.raises((TypeError, AttributeError)):
        first.policy._architecture["activation"] = "silu"
    np.testing.assert_array_equal(second.policy.logits(vector), before)
    assert not hasattr(first.policy, "__dict__")


def test_cached_transformer_layout_has_only_immutable_slotted_state():
    from boardcraft.inference.spec import TokenLayout

    layout = TokenLayout(get_game("dice_circuit").encoder().encoder_spec())
    with pytest.raises((TypeError, AttributeError)):
        layout.groups = ()
    assert not hasattr(layout, "__dict__")
    assert not hasattr(layout.groups[0], "__dict__")
    assert not hasattr(layout.groups[0].fields[0], "__dict__")
