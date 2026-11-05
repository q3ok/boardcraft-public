"""Numerically explicit float32 policy operations."""

import math
import numpy as np


def erf(x):
    """A&S 7.1.26 in float64: maximum absolute approximation error 1.5e-7.

    The GELU definition remains x*Phi(x), not the tanh GELU approximation.
    Source: https://personal.math.ubc.ca/~cbm/aands/page_299.htm
    """
    z = np.asarray(x, np.float64)
    t = 1.0 / (1.0 + 0.3275911 * np.abs(z))
    polynomial = (
        (((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t
        + 0.254829592
    ) * t
    return np.sign(z) * (1.0 - polynomial * np.exp(-z * z))


def activate(x, name):
    if name == "relu":
        return np.maximum(x, np.float32(0))
    if name == "leaky_relu":
        return np.where(x >= 0, x, x * np.float32(0.01))
    if name == "gelu":
        evaluated = erf(x / np.float32(math.sqrt(2))).astype(np.float32)
        return np.float32(0.5) * x * (np.float32(1) + evaluated)
    if name == "silu":
        return x * (np.float32(0.5) + np.float32(0.5) * np.tanh(np.float32(0.5) * x))
    raise ValueError("Unknown activation")


def linear(x, weights, prefix):
    return x @ weights[prefix + ".weight"].T + weights[prefix + ".bias"]


def mlp(x, weights, network, *, features=False):
    """Policy logits, or with ``features`` the trunk output that the policy (and an optional value head) reads."""
    for i in range(len(network["hidden"])):
        x = activate(linear(x, weights, f"trunk.layers.{i}"), network["activation"])
    return x if features else linear(x, weights, "pi")


def softmax(x):
    e = np.exp(x - x.max(axis=-1, keepdims=True))
    return e / e.sum(axis=-1, keepdims=True)
