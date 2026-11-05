"""Strict policy-only artifact loading and single/batched NumPy inference."""

import json
from dataclasses import dataclass
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping
from collections.abc import Mapping as MappingABC
from .archive import read_stored_arrays
import numpy as np
from .spec import TokenLayout, architecture, identities, weight_shapes
from .mlp import mlp, softmax
from .transformer import transformer
from .identity import canonical_tensor_digest
from .labels import display_name


@dataclass(frozen=True, slots=True)
class _FrozenWeights(MappingABC):
    """Immutable bytes with fresh array metadata for each consumer view."""

    _entries: Mapping

    def __getitem__(self, name):
        shape, data = self._entries[name]
        return np.frombuffer(data, dtype=np.float32).reshape(shape)

    def __iter__(self):
        return iter(self._entries)

    def __len__(self):
        return len(self._entries)


def validate_weights(encoder, network, weights):
    shapes = weight_shapes(encoder, network)
    if set(weights) != set(shapes):
        raise ValueError("Unexpected or missing policy arrays")
    result = {}
    for name, shape in shapes.items():
        array = weights[name]
        if (
            not isinstance(array, np.ndarray)
            or array.dtype != np.dtype("float32")
            or array.shape != shape
            or not np.isfinite(array).all()
        ):
            raise ValueError("Invalid policy array shape, dtype or values")
        result[name] = (shape, array.tobytes(order="C"))
    return _FrozenWeights(MappingProxyType(result))


def value_width(encoder, network):
    """Width of the trunk output that an optional value head reads."""
    network = architecture(network)
    if network["family"] == "transformer":
        return network["dim"]
    return network["hidden"][-1] if network["hidden"] else encoder.encoder_spec()["flat_size"]


@dataclass(frozen=True, slots=True, init=False)
class NumpyPolicy:
    _architecture: Mapping
    _weights: Mapping
    _input_size: int
    _layout: TokenLayout | None
    _value: Mapping | None

    def __init__(self, encoder, network, weights, *, value_weights=None):
        network = architecture(network)
        frozen_weights = validate_weights(encoder, network, weights)
        layout = (
            TokenLayout(encoder.encoder_spec())
            if network["family"] == "transformer"
            else None
        )
        object.__setattr__(self, "_architecture", _freeze(network))
        object.__setattr__(self, "_weights", frozen_weights)
        object.__setattr__(self, "_input_size", encoder.encoder_spec()["flat_size"])
        object.__setattr__(self, "_layout", layout)
        # An optional value head (v.weight, v.bias) on the trunk output. It is kept apart from the policy tensors, so
        # the policy digest and the chosen moves do not depend on it.
        value = None
        if value_weights is not None:
            width = value_width(encoder, network)
            shapes = {"v.weight": (1, width), "v.bias": (1,)}
            if set(value_weights) != set(shapes) or any(
                not isinstance(value_weights[k], np.ndarray) or value_weights[k].dtype != np.dtype("float32")
                or value_weights[k].shape != shape or not np.isfinite(value_weights[k]).all()
                for k, shape in shapes.items()
            ):
                raise ValueError("Invalid value head arrays")
            value = _FrozenWeights(MappingProxyType({k: (shapes[k], value_weights[k].tobytes(order="C")) for k in shapes}))
        object.__setattr__(self, "_value", value)

    @property
    def has_value(self):
        return self._value is not None

    def value(self, obs):
        """The artifact's value estimate of one encoded position, or None without a value head."""
        if self._value is None:
            return None
        x = np.array(obs, dtype=np.float32, copy=True)
        if x.ndim != 1 or x.shape[0] != self._input_size or not np.isfinite(x).all():
            raise ValueError("Expected one finite encoder vector")
        z = (
            mlp(x[None], self._weights, self._architecture, features=True)
            if self._layout is None
            else transformer(x[None], self._weights, self._architecture, self._layout, features=True)
        )
        result = float((z @ self._value["v.weight"].T + self._value["v.bias"])[0, 0])
        return result if np.isfinite(result) else None

    def logits(self, obs):
        x = np.array(obs, dtype=np.float32, copy=True)
        if (
            x.ndim not in (1, 2)
            or x.shape[-1] != self._input_size
            or not np.isfinite(x).all()
            or (x.ndim == 2 and not len(x))
        ):
            raise ValueError("Expected finite single or batched encoder vectors")
        single = x.ndim == 1
        if single:
            x = x[None]
        result = (
            mlp(x, self._weights, self._architecture)
            if self._layout is None
            else transformer(x, self._weights, self._architecture, self._layout)
        )
        if not np.isfinite(result).all():
            raise ValueError("Policy produced nonfinite logits")
        return result[0].copy() if single else result

    def probs(self, obs, mask):
        logits = self.logits(obs)
        mask = np.asarray(mask)
        if (
            mask.dtype != np.bool_
            or mask.shape != logits.shape
            or not mask.any(axis=-1).all()
        ):
            raise ValueError("A matching nonempty boolean legal mask is required")
        return softmax(np.where(mask, logits, -np.inf))


def _unique(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate metadata field")
        result[key] = value
    return result


def validate_description(value):
    """Public English prose: 1 to 600 ASCII characters, without markup or paths."""
    if (not isinstance(value, str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9 .,;!?'\"_()+\-]{0,599}", value)
            or value != value.strip()):
        raise ValueError("Invalid English model description")
    return value


def validate_metadata(metadata, encoder):
    expected = identities(encoder)
    keys = set(expected) | {"format", "architecture", "weights_sha256", "display_name"}
    if (
        not isinstance(metadata, dict)
        or set(metadata) - {"description", "value_head"} != keys
        or ("value_head" in metadata and metadata["value_head"] != {"format": 1})
        or type(metadata["format"]) is not int
        or metadata["format"] != 1
    ):
        raise ValueError("Unexpected inference metadata fields or format")
    if any(
        type(metadata[k]) is not type(v) or metadata[k] != v
        for k, v in expected.items()
    ):
        raise ValueError("Incompatible encoder, actions, game or rules")
    display_name(metadata["display_name"])
    if "description" in metadata:
        validate_description(metadata["description"])
    if not isinstance(metadata["weights_sha256"], str) or not re.fullmatch(
        "[0-9a-f]{64}", metadata["weights_sha256"]
    ):
        raise ValueError("Invalid weight digest")
    return architecture(metadata["architecture"])


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class LoadedPolicy:
    """Validated metadata owned by the exact policy loaded in the same operation."""

    policy: NumpyPolicy
    metadata: Mapping[str, object]
    policy_sha256: str


def load_artifact(metadata_path, weights_path, encoder):
    metadata_path, weights_path = Path(metadata_path), Path(weights_path)
    with metadata_path.open("rb") as stream:
        raw_metadata = stream.read(65537)
    if len(raw_metadata) > 65536:
        raise ValueError("Artifact metadata exceeds format size limit")
    metadata = json.loads(raw_metadata.decode("utf-8"), object_pairs_hook=_unique)
    network = validate_metadata(metadata, encoder)
    shapes = weight_shapes(encoder, network)
    # Optional value head (``value_head: {"format": 1}``): both v tensors live in the same archive.
    if "value_head" in metadata:
        shapes = {**shapes, "v.weight": (1, value_width(encoder, network)), "v.bias": (1,)}
    arrays = read_stored_arrays(weights_path, shapes, metadata["weights_sha256"])
    value = {k: a for k, a in arrays.items() if k.startswith("v.")}
    policy = NumpyPolicy(encoder, network, {k: a for k, a in arrays.items() if not k.startswith("v.")},
                         value_weights=value or None)
    return LoadedPolicy(
        policy, _freeze(metadata), canonical_tensor_digest(policy._weights)
    )


def load_model(metadata_path, weights_path, encoder):
    return load_artifact(metadata_path, weights_path, encoder).policy


def load_game_artifact(metadata_path, weights_path, module):
    """Load the closed Rentcraft format; other games retain their existing path.

    Rentcraft's optional ``value_head: {"format": 1}`` declares both v tensors
    in the same archive. They are validated and retained separately, outside the
    canonical policy tensor digest and architecture JSON.
    """
    if module.game_id != "rentcraft":
        return load_artifact(metadata_path, weights_path, module.encoder())
    from boardcraft.games.rentcraft.inference.policy import NumpyPolicy as RentPolicy, architecture as rent_architecture, weight_shapes as rent_shapes

    with Path(metadata_path).open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("Artifact metadata exceeds format size limit")
    metadata = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
    if not isinstance(metadata, dict):
        raise ValueError("Expected inference metadata object")
    profile = module.inference_profile(metadata.get("encoder_id"), metadata.get("encoder_version"))
    expected = profile.identities()
    has_value = "value_head" in metadata
    keys = set(expected) | {"format", "architecture", "weights_sha256", "display_name"}
    if has_value:
        keys.add("value_head")
    if (set(metadata) - {"description"} != keys or type(metadata.get("format")) is not int or metadata["format"] != 1
            or any(type(metadata[k]) is not type(v) or metadata[k] != v for k, v in expected.items())):
        raise ValueError("Incompatible Rentcraft artifact fields or profile")
    if has_value and (metadata["value_head"] != {"format": 1}
                      or type(metadata["value_head"].get("format")) is not int):
        raise ValueError("Unknown optional value head")
    name = metadata["display_name"]
    if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9 ._()\-]{0,119}", name)
            or name != name.strip()):
        raise ValueError("An explicit English display name is required")
    if "description" in metadata:
        validate_description(metadata["description"])
    checksum = metadata["weights_sha256"]
    if not isinstance(checksum, str) or not re.fullmatch("[0-9a-f]{64}", checksum):
        raise ValueError("Invalid weight digest")
    net = rent_architecture(metadata["architecture"])
    shapes = rent_shapes(profile, net, value_head=has_value)
    arrays = read_stored_arrays(Path(weights_path), shapes, checksum)
    value = {k: a for k, a in arrays.items() if k.startswith("v.")}
    policy = RentPolicy(profile, net, {k: a for k, a in arrays.items() if not k.startswith("v.")},
                        value_weights=value if has_value else None)
    return LoadedPolicy(policy, _freeze(metadata), canonical_tensor_digest(policy.weights))
