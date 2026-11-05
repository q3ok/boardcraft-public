"""Synthetic legacy forward contracts; no checkpoints or private imports."""
import importlib
import math

import numpy as np
import pytest

from boardcraft.games.rentcraft.inference.profiles import MINI, V7, resolve_profile
from boardcraft.games.rentcraft.inference.tokens import index_tables, token_layout


MLP = "rentcraft-legacy-mlp-v1"
TF = "rentcraft-legacy-transformer-v1"


def api():
    return importlib.import_module("boardcraft.games.rentcraft.inference.policy")


def network(family=MLP, **changes):
    result = {"family": family, "activation": "silu", "selection": "greedy"}
    result.update({"hidden": [2]} if family == MLP else {"layers": 1, "heads": 1, "dim": 2, "ff": 2})
    return {**result, **changes}


def weights(profile, net):
    return {k: np.zeros(s, np.float32) for k, s in api().weight_shapes(profile, net).items()}


@pytest.mark.parametrize("activation,want", [
    ("relu", [0., 2.]), ("leaky_relu", [-.02, 2.]),
    ("silu", [-0.238405844, 1.761594156]),
    ("gelu", [-0.045500264, 1.954499736]),
])
def test_mini_mlp_hand_computed_activation_and_source_body_names(activation, want):
    profile, net = resolve_profile(MINI), network(activation=activation)
    w = weights(profile, net)
    assert w["body.0.weight"].shape == (2, 1055)
    w["body.0.weight"][0, 0] = 1
    w["body.0.weight"][1, 1] = 1
    w["pi.weight"][:2] = np.eye(2)
    obs = np.zeros(1055, np.float32)
    obs[:2] = [-2, 2]
    policy = api().NumpyPolicy(profile, net, w)
    np.testing.assert_allclose(policy.logits(obs)[:2], want, atol=2e-7)
    assert np.all(policy.logits(obs)[2:] == 0)


def test_v7_mlp_pack_has_continuous_first_and_ordered_embedding8():
    profile, net = resolve_profile(V7), network(activation="relu")
    w = weights(profile, net)
    assert w["body.0.weight"].shape == (2, 16166)
    tables = index_tables(profile.encoder().fields)
    obs = np.zeros(3734, np.float32)
    used = np.concatenate([ix for _, _, ix in tables])
    cont = np.setdiff1d(np.arange(3734), used)
    obs[cont[0]] = 3
    table, vocab, ix = tables[0]
    assert w[f"emb_{table}.weight"].shape == (vocab, 8)
    obs[ix[0]] = 1
    w[f"emb_{table}.weight"][1] = np.arange(8)
    w["body.0.weight"][0, 0] = 2
    w["body.0.weight"][1, len(cont):len(cont)+8] = 1
    w["pi.weight"][:2] = np.eye(2)
    p = api().NumpyPolicy(profile, net, w)
    np.testing.assert_array_equal(p.logits(obs)[:2], [6, 28])
    packed = p.inputs(obs)
    assert packed.shape == (16166,)
    np.testing.assert_array_equal(packed[len(cont):len(cont)+8], np.arange(8))


def test_transformer_tokens_sum_shared_embeddings_card_globals_and_empty_keys():
    profile, net = resolve_profile(V7), network(TF)
    w = weights(profile, net)
    lay = token_layout(V7)
    assert lay.field_card.shape == (28, 27)
    w["cls"][:] = [11, 12]
    w["player_emb"][:] = [1, 2]
    w["space_emb"][:] = [3, 4]
    w["field_card_in.weight"][0, -2] = 7  # station flag
    w["global_w"][:] = 1
    w["global_b"][:] = [5, 6]
    w["emb_give.weight"][1] = [2, 3]
    w["emb_get.weight"][1] = [13, 17]
    obs = np.zeros(3734, np.float32)
    event = lay.fields.groups()[0]
    obs[event.base + event.index_cols["kind"]] = 1
    for col in ("give0", "give1"):
        obs[event.base + event.index_cols[col]] = 1
    obs[event.base + event.index_cols["get0"]] = 1
    obs[lay.globals[0][1][0]] = 9
    p = api().NumpyPolicy(profile, net, w)
    tokens, skip = p.tokens(obs)
    assert tokens.shape == (182, 2)
    np.testing.assert_array_equal(tokens[0], [11, 12])
    np.testing.assert_array_equal(tokens[1], [1, 2])
    np.testing.assert_array_equal(tokens[5:33, 0], 3 + 7 * lay.field_card[:, -2])
    np.testing.assert_array_equal(tokens[33], [14, 15])
    np.testing.assert_array_equal(tokens[34:38], np.tile([5, 6], (4, 1)))
    np.testing.assert_array_equal(tokens[38], [17, 23])
    assert not skip[:39].any() and skip[39:].all()


def test_transformer_hand_computed_pre_ln_attention_silu_final_original_cls():
    profile, net = resolve_profile(V7), network(TF)
    w = weights(profile, net)
    w["cls"][:] = [1, -1]
    prefix = "encoder_layers.layers.0."
    for name in ("norm1", "norm2"):
        w[prefix + name + ".weight"][:] = 1
    # Zero q/k => uniform over exactly 38 fixed keys; V copies normalized tokens.
    w[prefix + "self_attn.in_proj_weight"][4:] = np.eye(2)
    w[prefix + "self_attn.out_proj.weight"][:] = np.eye(2)
    w[prefix + "linear1.weight"][:] = np.eye(2)
    w[prefix + "linear2.weight"][:] = np.eye(2)
    w["norm.weight"][:] = [2, 3]
    w["norm.bias"][:] = [.25, -.5]
    w["pi.weight"][:2] = np.eye(2)
    p = api().NumpyPolicy(profile, net, w)
    obs = np.zeros(3734, np.float32)
    a = 1 + 1 / math.sqrt(1 + 1e-5) / 38
    b = a / math.sqrt(a*a + 1e-5)
    z = np.array([a + b/(1+math.exp(-b)), -a - b/(1+math.exp(b))])
    mean = z.mean()
    normalized = (z-mean) / math.sqrt(((z-mean)**2).mean()+1e-5)
    want = normalized * [2, 3] + [.25, -.5]
    np.testing.assert_allclose(p.logits(obs)[:2], want, atol=5e-7)
    # Empty entry keys must not contribute, even when their token has huge position.
    w["event_pos"][:] = [1000, -1000]
    np.testing.assert_array_equal(api().NumpyPolicy(profile, net, w).logits(obs), p.logits(obs))


def test_field_card_bias_is_added_before_card_is_added_to_space_token():
    profile, net = resolve_profile(V7), network(TF)
    w = weights(profile, net)
    w["space_emb"][:] = 1e8
    w["field_card_in.weight"][:, -2] = -1e8
    w["field_card_in.bias"][:] = 1
    tokens, _ = api().NumpyPolicy(profile, net, w).tokens(np.zeros(3734))
    stations = token_layout(V7).field_card[:, -2] == 1
    # Card = float32(-1e8 + 1) = -1e8, then space + card = 0.
    np.testing.assert_array_equal(tokens[5:33][stations], 0)


def test_profile_identity_binds_field_card_constants(monkeypatch):
    import boardcraft.games.rentcraft.inference.tokens as tokens
    profile = resolve_profile(V7)
    before = profile.identities()
    original = tokens.field_card_table
    def changed_card():
        card = original()
        card[0, 0] += 1
        return card
    monkeypatch.setattr(tokens, "field_card_table", changed_card)
    assert profile.identities()["encoder_layout_hash"] != before["encoder_layout_hash"]


def test_greedy_original_neg_softmax_first_tie_and_no_rng():
    profile, net = resolve_profile(MINI), network(hidden=[])
    w = weights(profile, net)
    w["pi.bias"][:] = -2e9
    p = api().NumpyPolicy(profile, net, w)
    obs, mask = np.zeros(1055, np.float32), np.zeros(221, bool)
    mask[[7, 12]] = True
    # NEG=-1e9 dominates legal -2e9; preserve original probabilities and best.
    probs = p.probs(obs, mask)
    assert probs[7] == probs[12] == 0 and probs[0] > 0
    assert p.best(obs, mask) == 7
    w["pi.bias"][:] = 0
    p = api().NumpyPolicy(profile, net, w)
    assert p.probs(obs, mask)[7] == .5 and p.best(obs, mask) == 7
    assert not hasattr(p, "sample")


@pytest.mark.parametrize("change", ["extra", "missing", "float64", "shape", "nan", "inf", "object", "partial_v"])
def test_strict_policy_tensor_allowlist(change):
    profile, net = resolve_profile(MINI), network()
    w = weights(profile, net)
    if change == "extra": w["aux_heads.any.weight"] = np.zeros((1, 2), np.float32)
    elif change == "missing": del w["pi.bias"]
    elif change == "partial_v": w["v.bias"] = np.zeros(1, np.float32)
    elif change == "shape": w["pi.bias"] = np.zeros(220, np.float32)
    elif change in ("float64", "object"): w["pi.bias"] = w["pi.bias"].astype(change)
    else: w["pi.bias"][0] = float(change)
    with pytest.raises(ValueError): api().NumpyPolicy(profile, net, w)


@pytest.mark.parametrize("bad", [
    {"selection": "sample"}, {"selection": None}, {"family": "mlp"},
    {"activation": "tanh"}, {"hidden": [True]}, {"hidden": [0]},
    {"hidden": [100000000]}, {"extra": 1},
])
def test_strict_architecture(bad):
    with pytest.raises(ValueError): api().weight_shapes(resolve_profile(MINI), network(**bad))


def test_value_is_separate_optional_immutable_and_identity_neutral():
    from boardcraft.inference.identity import canonical_tensor_digest
    profile, net = resolve_profile(MINI), network()
    w = weights(profile, net)
    value = {"v.weight": np.ones((1, 2), np.float32), "v.bias": np.ones(1, np.float32)}
    plain = api().NumpyPolicy(profile, net, w)
    p = api().NumpyPolicy(profile, net, w, value_weights=value)
    obs, mask = np.zeros(1055, np.float32), np.ones(221, bool)
    assert canonical_tensor_digest(p.weights) == canonical_tensor_digest(plain.weights)
    w["pi.bias"][:] = 100
    value["v.bias"][:] = 100
    np.testing.assert_array_equal(p.logits(obs), plain.logits(obs))
    assert p.best(obs, mask) == plain.best(obs, mask)
    for mapping in (p.weights, p.value_weights):
        for arr in mapping.values():
            with pytest.raises(ValueError): arr.setflags(write=True)
            arr.shape = (arr.size,)  # fresh array metadata cannot corrupt later reads
    assert p.weights["pi.weight"].shape == (221, 2)
    assert p.value_weights["v.bias"][0] == 1
    # Instances own all caches; sharing policy weights cannot share seat notes.
    first, second = profile.action_space(), profile.action_space()
    first.candidates = False
    assert second.candidates


@pytest.mark.parametrize("bad", [np.zeros(1054), np.full(1055, np.nan), np.zeros((1, 1055))])
def test_reject_invalid_single_observation(bad):
    profile, net = resolve_profile(MINI), network()
    p = api().NumpyPolicy(profile, net, weights(profile, net))
    with pytest.raises(ValueError): p.logits(bad)


@pytest.mark.parametrize("bad", [np.zeros(221, bool), np.ones(221), np.ones(220, bool)])
def test_reject_invalid_mask(bad):
    profile, net = resolve_profile(MINI), network()
    p = api().NumpyPolicy(profile, net, weights(profile, net))
    with pytest.raises(ValueError): p.best(np.zeros(1055), bad)


@pytest.mark.parametrize("family", [MLP, TF])
@pytest.mark.parametrize("bad", [-1, .5, 999, np.inf])
def test_v7_rejects_invalid_embedding_indices_before_lookup(family, bad):
    profile, net = resolve_profile(V7), network(family)
    w = weights(profile, net)
    obs = np.zeros(3734, np.float32)
    obs[index_tables(profile.encoder().fields)[0][2][0]] = bad
    with pytest.raises(ValueError): api().NumpyPolicy(profile, net, w).logits(obs)


@pytest.mark.parametrize("bad", [
    {"heads": 0}, {"dim": 1}, {"heads": 3}, {"layers": 1025},
    {"layers": True}, {"ff": -1}, {"selection": "sample"}, {"family": "transformer"},
])
def test_rejects_invalid_transformer_architecture(bad):
    net = {**network(TF), **bad}
    with pytest.raises(ValueError): api().weight_shapes(resolve_profile(V7), net)


def test_total_tensor_limit_includes_optional_value_head_without_allocating_weights():
    from boardcraft.inference.spec import MAX_WEIGHT_BYTES
    assert MAX_WEIGHT_BYTES == 256 * 1024 * 1024
    # mini one-layer policy: (1055 + 1 + 221)*width + 221 floats.
    width = (MAX_WEIGHT_BYTES // 4 - 221) // 1277
    profile, net = resolve_profile(MINI), network(hidden=[width])
    api().weight_shapes(profile, net)
    with pytest.raises(ValueError, match="size limit"):
        api().weight_shapes(profile, net, value_head=True)


@pytest.mark.parametrize("change", ["missing", "extra", "dtype", "shape", "nan", "object"])
def test_optional_value_head_has_its_own_strict_allowlist(change):
    profile, net = resolve_profile(MINI), network()
    w = weights(profile, net)
    v = {"v.weight": np.zeros((1, 2), np.float32), "v.bias": np.zeros(1, np.float32)}
    if change == "missing": del v["v.weight"]
    elif change == "extra": v["aux_heads.x.bias"] = np.zeros(1, np.float32)
    elif change == "dtype": v["v.bias"] = v["v.bias"].astype(np.float64)
    elif change == "shape": v["v.weight"] = np.zeros((2, 1), np.float32)
    elif change == "nan": v["v.bias"][0] = np.nan
    else: v["v.bias"] = np.array([object()], object)
    with pytest.raises(ValueError): api().NumpyPolicy(profile, net, w, value_weights=v)
