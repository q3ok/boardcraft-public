"""Strict legacy Rentcraft policy inference using NumPy and synthetic-safe inputs."""
import math
from dataclasses import dataclass
from types import MappingProxyType
from collections.abc import Mapping

import numpy as np

from boardcraft.inference.model import _FrozenWeights, _freeze
from boardcraft.inference.spec import MAX_WEIGHT_BYTES, ACTIVATIONS
from .profiles import MINI, V7, ModelProfile, resolve_profile
from .tokens import emb_table, index_tables, token_layout, tensor_table

MLP = "rentcraft-legacy-mlp-v1"
TRANSFORMER = "rentcraft-legacy-transformer-v1"
NEG = -1e9


def architecture(value):
    if not isinstance(value, dict):
        raise ValueError("Architecture must be an object")
    family = value.get("family")
    fields = {"family", "activation", "selection"} | (
        {"hidden"} if family == MLP else {"layers", "heads", "dim", "ff"})
    if (family not in (MLP, TRANSFORMER) or set(value) != fields
            or value["activation"] not in ACTIVATIONS or value["selection"] != "greedy"):
        raise ValueError("Unknown Rentcraft architecture or selection")
    widths = value["hidden"] if family == MLP else [value[k] for k in ("layers", "heads", "dim", "ff")]
    if (not isinstance(widths, list) or len(widths) > 1024
            or any(type(n) is not int or not 0 < n <= MAX_WEIGHT_BYTES // 4 for n in widths)):
        raise ValueError("Invalid network dimensions")
    if family == TRANSFORMER and (value["layers"] > 1024 or value["dim"] < 2 or value["dim"] % value["heads"]):
        raise ValueError("Invalid transformer dimensions")
    return {**value, **({"hidden": list(widths)} if family == MLP else {})}


def _profile(profile):
    if not isinstance(profile, ModelProfile) or profile != resolve_profile(profile.id, profile.version):
        raise ValueError("Unknown Rentcraft input profile")
    return profile


def weight_shapes(profile, network, *, value_head=False):
    profile, net = _profile(profile), architecture(network)
    result = {}

    def linear(name, source, target):
        result[name + ".weight"] = (target, source)
        result[name + ".bias"] = (target,)

    def norm(name, dim):
        result[name + ".weight"] = (dim,)
        result[name + ".bias"] = (dim,)

    if net["family"] == MLP:
        width = profile.size
        if profile.id == V7:
            tables = index_tables(profile.encoder().fields)
            width += sum(len(ix) * 7 for _, _, ix in tables)
            for table, vocab, _ in tables:
                result[f"emb_{tensor_table(table)}.weight"] = (vocab, 8)
        for i, target in enumerate(net["hidden"]):
            linear(f"body.{2*i}", width, target)
            width = target
    else:
        lay, width = token_layout(profile.id), net["dim"]
        linear("player_in", lay.player_idx.shape[1], width)
        linear("space_in", lay.space_idx.shape[1], width)
        result["player_emb"], result["space_emb"] = (4, width), (28, width)
        result["cls"] = (1, width)
        result["global_w"] = (sum(len(ix) for _, ix in lay.globals), width)
        result["global_b"] = (len(lay.globals), width)
        if profile.id == V7:
            linear("field_card_in", 27, width)
            for g in lay.fields.groups():
                linear(g.kind + "_in", len(g.cont_cols), width)
                result[g.kind + "_pos"] = (g.n, width)
            for table, vocab, _ in index_tables(lay.fields):
                result[f"emb_{tensor_table(table)}.weight"] = (vocab, width)
        for i in range(net["layers"]):
            p = f"encoder_layers.layers.{i}."
            norm(p + "norm1", width)
            norm(p + "norm2", width)
            result[p + "self_attn.in_proj_weight"] = (3 * width, width)
            result[p + "self_attn.in_proj_bias"] = (3 * width,)
            linear(p + "self_attn.out_proj", width, width)
            linear(p + "linear1", width, net["ff"])
            linear(p + "linear2", net["ff"], width)
        norm("norm", width)
    linear("pi", width, 221)
    if value_head:
        linear("v", width, 1)
    if len(result) > 65535 or sum(math.prod(s) * 4 for s in result.values()) > MAX_WEIGHT_BYTES:
        raise ValueError("Policy exceeds inference format size limit")
    return result


def _weights(shapes, weights):
    if not isinstance(weights, Mapping) or set(shapes) != set(weights):
        raise ValueError("Unexpected or missing Rentcraft arrays")
    entries = {}
    for name, shape in shapes.items():
        a = weights[name]
        if (not isinstance(a, np.ndarray) or a.dtype != np.dtype("float32")
                or a.shape != shape or not np.isfinite(a).all()):
            raise ValueError("Invalid Rentcraft array shape, dtype or values")
        entries[name] = (shape, a.tobytes(order="C"))
    return _FrozenWeights(MappingProxyType(entries))


def _activation(name, z):
    if name == "relu":
        return np.maximum(z, 0.0)
    if name == "leaky_relu":
        return np.where(z >= 0, z, z * np.float32(0.01))
    if name == "silu":
        return z * (0.5 + 0.5 * np.tanh(0.5 * z))
    x = np.asarray(z / np.sqrt(2.0), np.float64)
    t = 1.0 / (1.0 + 0.3275911 * np.abs(x))
    a1, a2, a3, a4, a5 = (0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429)
    erf = np.sign(x) * (1.0 - ((((a5*t+a4)*t+a3)*t+a2)*t+a1)*t*np.exp(-x*x))
    return (0.5 * z * (1.0 + erf)).astype(np.float32)


def _norm(x, g, b):
    mu = x.mean(-1, keepdims=True)
    var = ((x - mu) ** 2).mean(-1, keepdims=True)
    return (x - mu) / np.sqrt(var + 1e-5) * g + b


@dataclass(frozen=True, slots=True, init=False)
class NumpyPolicy:
    """Immutable policy; seat histories and draft state belong to its caller."""
    profile: ModelProfile
    architecture: Mapping
    weights: Mapping
    value_weights: Mapping
    _layout: object
    _tables: tuple
    _cont: np.ndarray

    def __init__(self, profile, network, weights, *, value_weights=None):
        profile, net = _profile(profile), architecture(network)
        shapes = weight_shapes(profile, net, value_head=value_weights is not None)
        v_shapes = {k: s for k, s in shapes.items() if k.startswith("v.")}
        p_shapes = {k: s for k, s in shapes.items() if not k.startswith("v.")}
        object.__setattr__(self, "profile", profile)
        object.__setattr__(self, "architecture", _freeze(net))
        object.__setattr__(self, "weights", _weights(p_shapes, weights))
        object.__setattr__(self, "value_weights", _weights(v_shapes, {} if value_weights is None else value_weights))
        lay = token_layout(profile.id) if net["family"] == TRANSFORMER else None
        tables = tuple(index_tables(profile.encoder().fields)) if profile.id == V7 else ()
        used = np.concatenate([ix for _, _, ix in tables]) if tables else np.zeros(0, np.int64)
        object.__setattr__(self, "_layout", lay)
        object.__setattr__(self, "_tables", tables)
        object.__setattr__(self, "_cont", np.setdiff1d(np.arange(profile.size), used))

    def _observation(self, obs):
        x = np.array(obs, dtype=np.float32, copy=True)
        if x.shape != (self.profile.size,) or not np.isfinite(x).all():
            raise ValueError("Expected a finite single Rentcraft encoder vector")
        for _, vocab, ix in self._tables:
            codes = x[ix]
            if np.any(codes < 0) or np.any(codes >= vocab) or np.any(codes != np.floor(codes)):
                raise ValueError("Invalid Rentcraft categorical code")
        return x

    def inputs(self, obs):
        x = self._observation(obs)
        if not self._tables:
            return x
        return np.concatenate([x[self._cont], *[
            self.weights[f"emb_{tensor_table(t)}.weight"][x[ix].astype(np.int64)].ravel() for t, _, ix in self._tables]])

    def tokens(self, obs):
        if self._layout is None:
            raise ValueError("Tokens require a transformer")
        lay, w = self._layout, self.weights
        x = np.append(self._observation(obs), np.float32(0.0))
        rows = [w["cls"], x[lay.player_idx] @ w["player_in.weight"].T + w["player_in.bias"] + w["player_emb"],
                x[lay.space_idx] @ w["space_in.weight"].T + w["space_in.bias"] + w["space_emb"]]
        if getattr(lay, "field_card", None) is not None:
            card = lay.field_card.astype(np.float32) @ w["field_card_in.weight"].T + w["field_card_in.bias"]
            rows[2] = rows[2] + card
        if lay.globals:
            idx = np.concatenate([ix for _, ix in lay.globals])
            seg = np.zeros((len(lay.globals), len(idx)), np.float32)
            offset = 0
            for i, (_, ix) in enumerate(lay.globals):
                seg[i, offset:offset+len(ix)] = 1.0
                offset += len(ix)
            rows.append((x[idx] * seg) @ w["global_w"] + w["global_b"])
        skip = [np.zeros(sum(len(r) for r in rows), bool)]
        if self.profile.id == V7:
            for g in lay.fields.groups():
                e = x[lay.entry_idx[g.kind]]
                t = e[:, sorted(g.cont_cols.values())] @ w[g.kind + "_in.weight"].T + w[g.kind + "_in.bias"] + w[g.kind + "_pos"]
                groups = {}
                for col, c in g.index_cols.items():
                    groups.setdefault(emb_table(col), []).append(c)
                for table, cols in groups.items():
                    t = t + w[f"emb_{tensor_table(table)}.weight"][e[:, cols].astype(np.int64)].sum(axis=1)
                rows.append(t)
                skip.append(e[:, g.index_cols[g.key]] == 0)
        return np.concatenate(rows).astype(np.float32), np.concatenate(skip)

    def _attend(self, q, k, v, skip):
        h = self.architecture["heads"]
        dh = q.shape[1] // h
        qh = q.reshape(q.shape[0], h, dh).transpose(1, 0, 2)
        kh = k.reshape(k.shape[0], h, dh).transpose(1, 2, 0)
        vh = v.reshape(v.shape[0], h, dh).transpose(1, 0, 2)
        a = qh @ kh / np.sqrt(dh)
        a[..., skip] = -np.inf
        a = np.exp(a - a.max(-1, keepdims=True))
        a /= a.sum(-1, keepdims=True)
        return (a @ vh).transpose(1, 0, 2).reshape(q.shape[0], -1)

    def logits(self, obs):
        result = self.features(obs) @ self.weights["pi.weight"].T + self.weights["pi.bias"]
        if not np.isfinite(result).all():
            raise ValueError("Policy produced nonfinite logits")
        return result

    def value(self, obs):
        """The artifact's own value estimate of the position, or None when it carries no value head."""
        if not self.value_weights:
            return None
        result = float((self.features(obs) @ self.value_weights["v.weight"].T + self.value_weights["v.bias"])[0])
        return result if np.isfinite(result) else None

    def features(self, obs):
        w, net = self.weights, self.architecture
        if self._layout is None:
            z = self.inputs(obs)
            for i in range(len(net["hidden"])):
                z = _activation(net["activation"], z @ w[f"body.{2*i}.weight"].T + w[f"body.{2*i}.bias"])
        else:
            hdn, skip = self.tokens(obs)
            d = hdn.shape[1]
            for i in range(net["layers"]):
                p = f"encoder_layers.layers.{i}."
                last = i == net["layers"] - 1
                y = _norm(hdn, w[p + "norm1.weight"], w[p + "norm1.bias"])
                qkv = y @ w[p + "self_attn.in_proj_weight"].T.copy() + w[p + "self_attn.in_proj_bias"]
                q = qkv[:1, :d] if last else qkv[:, :d]
                att = self._attend(q, qkv[:, d:2*d], qkv[:, 2*d:], skip) @ w[p + "self_attn.out_proj.weight"].T.copy() + w[p + "self_attn.out_proj.bias"]
                hdn = (hdn[:1] if last else hdn) + att
                y = _norm(hdn, w[p + "norm2.weight"], w[p + "norm2.bias"])
                hdn = hdn + _activation(net["activation"], y @ w[p + "linear1.weight"].T.copy() + w[p + "linear1.bias"]) @ w[p + "linear2.weight"].T.copy() + w[p + "linear2.bias"]
            z = _norm(hdn[0], w["norm.weight"], w["norm.bias"])
        return z

    def probs(self, obs, mask):
        mask = np.asarray(mask)
        if mask.dtype != np.bool_ or mask.shape != (221,) or not mask.any():
            raise ValueError("Expected a nonempty boolean Rentcraft mask")
        logits = np.where(mask, self.logits(obs), NEG)
        e = np.exp(logits - logits.max())
        return e / e.sum()

    def best(self, obs, mask):
        return int(np.argmax(np.where(mask, self.probs(obs, mask), -1.0)))
