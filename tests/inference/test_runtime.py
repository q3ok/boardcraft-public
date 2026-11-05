"""Policy-only runtime fixtures use synthetic arrays and actual game codecs."""

import importlib
import math
import numpy as np
import pytest
from boardcraft.contracts import GameConfig
from boardcraft.registry import get_game


def runtime():
    try:
        return importlib.import_module("boardcraft.inference.model")
    except ModuleNotFoundError:
        pytest.fail("NumPy policy runtime is missing")


def test_vector_erf_evaluator_against_scalar_reference():
    runtime()
    from boardcraft.inference import mlp

    assert hasattr(mlp, "erf"), "Bounded vectorized erf evaluator is missing"
    values = np.concatenate(
        (
            np.linspace(-10, 10, 200001),
            np.linspace(-1e-7, 1e-7, 10001),
            [-1e4, -100, -20, 0, 20, 100, 1e4],
        )
    )
    expected = np.array([math.erf(float(value)) for value in values])
    assert np.max(np.abs(mlp.erf(values) - expected)) < 1.5e-7
    assert mlp.erf(np.array([0.0]))[0] == 0


def test_linear_batch_mask_and_array_ownership():
    module = runtime()
    enc = get_game("dice_circuit").encoder()
    size, actions = enc.encoder_spec()["flat_size"], enc.action_spec()["size"]
    weights = {
        "pi.weight": np.zeros((actions, size), np.float32),
        "pi.bias": np.arange(actions, dtype=np.float32),
    }
    policy = module.NumpyPolicy(
        enc, {"family": "mlp", "hidden": [], "activation": "relu"}, weights
    )
    weights["pi.bias"][:] = 0
    x = np.zeros((2, size), np.float32)
    mask = np.zeros((2, actions), bool)
    mask[0, :2] = True
    mask[1, -2:] = True
    probs = policy.probs(x, mask)
    np.testing.assert_allclose(probs.sum(-1), 1, atol=1e-6)
    assert np.all(probs[~mask] == 0)
    assert probs[0, 1] > probs[0, 0]
    assert policy.logits(x[0]).shape == (actions,)
    for bad in (
        np.zeros(actions, bool),
        np.ones((1, actions), bool),
        np.ones((2, actions), np.int32),
    ):
        with pytest.raises(ValueError):
            policy.probs(x, bad)
    for bad in (np.zeros(size + 1), np.full(size, np.nan), np.zeros((1, 1, size))):
        with pytest.raises(ValueError):
            policy.logits(bad)


def test_observation_only_bot_uses_actual_legal_actions():
    module = runtime()
    from boardcraft.inference.bot import InferenceBot

    game = get_game("dice_circuit")
    enc = game.encoder()
    weights = {
        "pi.weight": np.zeros(
            (enc.action_spec()["size"], enc.encoder_spec()["flat_size"]), np.float32
        ),
        "pi.bias": np.zeros(enc.action_spec()["size"], np.float32),
    }
    policy = module.NumpyPolicy(
        enc, {"family": "mlp", "hidden": [], "activation": "silu"}, weights
    )
    bot = InferenceBot(enc, policy, seed=4)
    engine = game.new(GameConfig("dice_circuit", "base", 2), 9)
    decision = engine.decision()
    legal = engine.legal_actions(decision.player)
    choice = bot.choose(engine.observe(decision.player), legal)
    assert choice.action in legal
    with pytest.raises(ValueError):
        bot.choose(engine.observe(decision.player), ())


def stored_arrays(path, arrays):
    """Emit the existing format, including Python 3.10's ZIP64 correction."""
    import struct
    import zipfile

    class StoredInfo(zipfile.ZipInfo):
        def FileHeader(self, zip64=None):
            name = self.filename.encode("ascii")
            return (struct.pack("<4s5H3I2H", b"PK\x03\x04", 45, 0, 0, 0, 33,
                                self.CRC, 0xFFFFFFFF, 0xFFFFFFFF, len(name), 20)
                    + name + struct.pack("<HHQQ", 1, 16, self.file_size, self.compress_size))

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, array in arrays.items():
            info = StoredInfo(name + ".npy")
            info.create_version = info.extract_version = 45
            info.external_attr = 0o600 << 16
            with archive.open(info, "w", force_zip64=True) as stream:
                # Object fixture is inert serialized data; the runtime must refuse it.
                np.lib.format.write_array(stream, array, allow_pickle=True)


def rent_artifact(tmp_path, *, value=False, profile_id=None, family=None):
    import hashlib
    import json
    from boardcraft.games.rentcraft.inference.profiles import MINI, resolve_profile
    from boardcraft.games.rentcraft.inference import policy as rent
    profile = resolve_profile(profile_id or MINI)
    net = {"family": family or "rentcraft-legacy-mlp-v1", "activation": "relu",
           "hidden": [2], "selection": "greedy"}
    if net["family"] == "rentcraft-legacy-transformer-v1":
        del net["hidden"]
        net.update(dim=2, heads=1, layers=1, ff=2)
    arrays = {k: np.zeros(s, np.float32) for k, s in rent.weight_shapes(profile, net).items()}
    if value:
        arrays.update({"v.weight": np.ones((1, 2), np.float32), "v.bias": np.ones(1, np.float32)})
    path = tmp_path / "weights.npz"
    stored_arrays(path, arrays)
    metadata = {**profile.identities(), "format": 1, "architecture": net,
                "display_name": "Synthetic Rent", "weights_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    if value: metadata["value_head"] = {"format": 1}
    meta = tmp_path / "metadata.json"
    meta.write_text(json.dumps(metadata), encoding="utf-8")
    return meta, path, metadata, arrays


@pytest.mark.parametrize("profile_id", ["rentcraft-legacy-blocks-mini-evo5", "rentcraft-visible-v7-offers8"])
@pytest.mark.parametrize("family", ["rentcraft-legacy-mlp-v1", "rentcraft-legacy-transformer-v1"])
def test_game_loader_value_archive_optional_and_policy_digest_neutral(tmp_path, profile_id, family):
    module = runtime()
    assert hasattr(module, "load_game_artifact"), "Game-aware strict loader is missing"
    meta, path, _, _ = rent_artifact(tmp_path, profile_id=profile_id, family=family)
    plain = module.load_game_artifact(meta, path, get_game("rentcraft"))
    meta, path, _, _ = rent_artifact(tmp_path, value=True, profile_id=profile_id, family=family)
    valued = module.load_game_artifact(meta, path, get_game("rentcraft"))
    assert plain.policy_sha256 == valued.policy_sha256
    assert plain.metadata["weights_sha256"] != valued.metadata["weights_sha256"]
    assert not plain.policy.value_weights and set(valued.policy.value_weights) == {"v.weight", "v.bias"}
    obs = np.zeros(plain.metadata["input_size"])
    mask = np.ones(221, bool)
    np.testing.assert_array_equal(plain.policy.logits(obs), valued.policy.logits(obs))
    np.testing.assert_array_equal(plain.policy.probs(obs, mask), valued.policy.probs(obs, mask))
    assert plain.policy.best(obs, mask) == valued.policy.best(obs, mask)


@pytest.mark.parametrize("mutation", [
    "foreign_profile", "foreign_family", "selection", "game", "version", "layout", "extra_meta",
    "duplicate", "oversize", "partial_value", "undeclared_value", "declared_missing_value",
    "extra_array", "missing", "dtype", "shape", "nan", "object", "compressed", "digest",
])
def test_game_loader_strict_rejections(tmp_path, mutation):
    import hashlib
    import json
    module = runtime()
    assert hasattr(module, "load_game_artifact"), "Game-aware strict loader is missing"
    meta, path, data, arrays = rent_artifact(tmp_path, value=True)
    if mutation == "foreign_profile": data["encoder_id"] = "dice-circuit-visible"
    elif mutation == "foreign_family": data["architecture"]["family"] = "mlp"
    elif mutation == "selection": data["architecture"]["selection"] = "sample"
    elif mutation == "game": data["game_id"] = "dice_circuit"
    elif mutation == "version": data["encoder_version"] = True
    elif mutation == "layout": data["encoder_layout_hash"] = "0" * 64
    elif mutation == "extra_meta": data["private_config"] = {}
    elif mutation == "partial_value": del arrays["v.bias"]
    elif mutation == "undeclared_value": del data["value_head"]
    elif mutation == "declared_missing_value":
        del arrays["v.weight"], arrays["v.bias"]
    elif mutation == "extra_array": arrays["aux_heads.x.bias"] = np.zeros(1, np.float32)
    elif mutation == "missing": del arrays["pi.bias"]
    elif mutation == "dtype": arrays["pi.bias"] = arrays["pi.bias"].astype(np.float64)
    elif mutation == "shape": arrays["pi.bias"] = np.zeros(222, np.float32)
    elif mutation == "nan": arrays["v.bias"][0] = np.nan
    elif mutation == "object": arrays["v.bias"] = np.array([object()], dtype=object)
    if mutation == "compressed": np.savez_compressed(path, **arrays)
    else: stored_arrays(path, arrays)
    data["weights_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    if mutation == "digest": data["weights_sha256"] = "0" * 64
    raw = json.dumps(data)
    if mutation == "duplicate": raw = raw[:-1] + ',"format":1}'
    elif mutation == "oversize": raw += " " * 65537
    meta.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError): module.load_game_artifact(meta, path, get_game("rentcraft"))


def test_game_loader_dice_delegates_existing_format_without_new_fields(tmp_path):
    import hashlib
    import json
    from boardcraft.inference.spec import identities
    module = runtime()
    assert hasattr(module, "load_game_artifact"), "Game-aware strict loader is missing"
    game = get_game("dice_circuit")
    enc = game.encoder()
    size, count = enc.encoder_spec()["flat_size"], enc.action_spec()["size"]
    path, meta = tmp_path / "weights.npz", tmp_path / "metadata.json"
    stored_arrays(path, {"pi.weight": np.zeros((count, size), np.float32), "pi.bias": np.zeros(count, np.float32)})
    data = {**identities(enc), "format": 1, "display_name": "Synthetic Dice",
            "architecture": {"family": "mlp", "hidden": [], "activation": "relu"},
            "weights_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    meta.write_text(json.dumps(data), encoding="utf-8")
    old = module.load_artifact(meta, path, enc)
    new = module.load_game_artifact(meta, path, game)
    assert old.policy_sha256 == new.policy_sha256 and old.metadata == new.metadata


def test_rent_loader_reads_metadata_once_and_forbids_pickle(tmp_path, monkeypatch):
    from pathlib import Path
    module = runtime()
    meta, path, _, _ = rent_artifact(tmp_path, value=True)
    original_open, original_load = Path.open, np.load
    opens, pickle_flags = [], []
    def opened(self, *args, **kwargs):
        if self == meta: opens.append(self)
        return original_open(self, *args, **kwargs)
    def load(*args, **kwargs):
        pickle_flags.append(kwargs.get("allow_pickle"))
        return original_load(*args, **kwargs)
    monkeypatch.setattr(Path, "open", opened)
    monkeypatch.setattr(np, "load", load)
    loaded = module.load_game_artifact(meta, path, get_game("rentcraft"))
    assert len(opens) == 1 and pickle_flags and all(flag is False for flag in pickle_flags)
    with pytest.raises(TypeError): loaded.metadata["value_head"]["format"] = 2
    with pytest.raises((TypeError, AttributeError)): loaded.policy.architecture["selection"] = "sample"
