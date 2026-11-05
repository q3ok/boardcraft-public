"""Descriptor-derived entity projections and pre-normalized attention."""

import numpy as np
from .mlp import activate, linear, softmax


def group_features(x, group):
    present = x[:, group.presence] != 0
    parts = []
    for field in group.fields:
        valid = (x[:, field.presence] != 0) & present[..., None]
        values = np.where(valid, x[:, field.indices], np.float32(0))
        if field.categories is not None:
            raw = values * np.asarray(field.divisor, np.float32)
            codes = np.rint(raw)
            if np.any(
                valid
                & (
                    (np.abs(raw - codes) > 1e-5)
                    | (codes < 0)
                    | (codes > field.categories)
                )
            ):
                raise ValueError("Invalid present categorical code")
            values = (
                np.eye(field.categories + 1, dtype=np.float32)[codes.astype(np.int64)]
                * valid[..., None]
            )
            values = values.reshape(*values.shape[:2], -1)
        parts.append(values)
    return np.concatenate(parts, axis=-1), present


def norm(x, weights, prefix):
    centered = x - x.mean(axis=-1, keepdims=True)
    return (
        centered
        / np.sqrt(
            np.mean(centered * centered, axis=-1, keepdims=True) + np.float32(1e-5)
        )
        * weights[prefix + ".weight"]
        + weights[prefix + ".bias"]
    )


def transformer(x, weights, network, layout, *, features=False):
    batch, dim, heads = len(x), network["dim"], network["heads"]
    tokens = [np.broadcast_to(weights["trunk.cls"], (batch, 1, dim))]
    present = [np.ones((batch, 1), bool)]
    for index, group in enumerate(layout.groups):
        group_x, mask = group_features(x, group)
        prefix = f"trunk.groups.{index}"
        tokens.append(linear(group_x, weights, prefix) + weights[prefix + ".position"])
        present.append(mask)
    z = np.concatenate(tokens, axis=1)
    mask = np.concatenate(present, axis=1)
    count = z.shape[1]
    for index in range(network["layers"]):
        prefix = f"trunk.layers.{index}"
        h = norm(z, weights, prefix + ".norm1")
        qkv = linear(h, weights, prefix + ".qkv").reshape(
            batch, count, 3, heads, dim // heads
        )
        q, k, v = (qkv[:, :, i].transpose(0, 2, 1, 3) for i in range(3))
        scores = (q @ k.swapaxes(-2, -1)) / np.float32(np.sqrt(dim // heads))
        attention = softmax(np.where(mask[:, None, None, :], scores, -np.inf))
        attended = (attention @ v).transpose(0, 2, 1, 3).reshape(batch, count, dim)
        z = z + linear(attended, weights, prefix + ".out")
        h = norm(z, weights, prefix + ".norm2")
        z = z + linear(
            activate(linear(h, weights, prefix + ".ff1"), network["activation"]),
            weights,
            prefix + ".ff2",
        )
    z = norm(z[:, 0], weights, "trunk.norm")
    return z if features else linear(z, weights, "pi")
