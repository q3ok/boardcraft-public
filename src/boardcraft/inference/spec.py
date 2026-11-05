"""Inference format and token coordinates derived from the canonical encoder."""

from dataclasses import dataclass
import hashlib
import json
import math

MAX_WEIGHT_BYTES = 256 * 1024 * 1024
ACTIVATIONS = ("relu", "leaky_relu", "gelu", "silu")


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def architecture(value):
    if not isinstance(value, dict):
        raise ValueError("Architecture must be an object")
    fields = (
        {"family", "activation", "hidden"}
        if value.get("family") == "mlp"
        else {"family", "activation", "layers", "heads", "dim", "ff"}
    )
    if set(value) != fields or value.get("activation") not in ACTIVATIONS:
        raise ValueError("Incomplete or unknown inference architecture")
    if value["family"] == "mlp":
        widths = value["hidden"]
        if not isinstance(widths, list):
            raise ValueError("Explicit hidden widths are required")
    elif value["family"] == "transformer":
        widths = [value[k] for k in ("layers", "heads", "dim", "ff")]
    else:
        raise ValueError("Unknown network family")
    if any(type(n) is not int or n <= 0 or n > MAX_WEIGHT_BYTES // 4 for n in widths):
        raise ValueError(
            "Architecture widths must be positive integers within format limits"
        )
    if value["family"] == "transformer" and value["dim"] < 2:
        raise ValueError("Layer normalization requires a dimension of at least two")
    if value["family"] == "transformer" and value["dim"] % value["heads"]:
        raise ValueError("Dimension must be divisible by heads")
    if len(widths) > 1024 or value.get("layers", 0) > 1024:
        raise ValueError("Architecture exceeds format depth limit")
    return json.loads(json.dumps(value))


@dataclass(frozen=True, slots=True)
class TokenField:
    indices: tuple[tuple[int, ...], ...]
    presence: tuple[tuple[int, ...], ...]
    divisor: tuple[float, ...]
    categories: int | None


@dataclass(frozen=True, slots=True)
class TokenGroup:
    entities: tuple[str, ...]
    presence: tuple[int, ...]
    fields: tuple[TokenField, ...]
    width: int


@dataclass(frozen=True, slots=True, init=False)
class TokenLayout:
    """One token per descriptor entity, with per-coordinate explicit presence."""

    groups: tuple[TokenGroup, ...]
    size: int

    def __init__(self, spec):
        fields = spec["fields"]
        by_name = {f["name"]: f for f in fields}
        size = spec["flat_size"]
        if len(by_name) != len(fields) or [
            i for f in fields for i in range(f["offset"], f["offset"] + f["width"])
        ] != list(range(size)):
            raise ValueError("Invalid canonical field coverage")
        groups = []
        covered = []
        for group in spec["groups"]:
            entities = group["entity_ids"]
            rows = [
                [
                    f
                    for f in fields
                    if f["group"] == group["id"] and f["entity"] == entity
                ]
                for entity in entities
            ]
            if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
                raise ValueError("Inconsistent entity fields")
            actual_indices = [
                [i for f in row for i in range(f["offset"], f["offset"] + f["width"])]
                for row in rows
            ]
            presence_indices = []
            for entity in entities:
                field = by_name.get(f"{entity}.present")
                if (
                    field is None
                    or field["entity"] != entity
                    or field["group"] != group["id"]
                    or field["width"] != 1
                    or field["value_kind"] != "boolean"
                ):
                    raise ValueError(
                        "Entity presence must be its own scalar boolean field"
                    )
                presence_indices.append(field["offset"])
            if (
                actual_indices != group["feature_indices"]
                or group["presence_indices"] != presence_indices
                or any(type(i) is not int for i in group["presence_indices"])
            ):
                raise ValueError("Group maps differ from field descriptors")
            covered.extend(i for row in actual_indices for i in row)
            descriptors = []
            width = 0
            for column in zip(*rows):
                first = column[0]
                signature = lambda f: (
                    f["width"],
                    f["value_kind"],
                    f["normalization"],
                    f.get("vocabulary"),
                    f["encoding"],
                )
                if any(signature(f) != signature(first) for f in column):
                    raise ValueError(
                        "Shared projection fields must have identical semantics"
                    )
                indices, masks = [], []
                for f in column:
                    indices.append(tuple(range(f["offset"], f["offset"] + f["width"])))
                    presence = by_name.get(f["presence_field"])
                    if presence is None or presence["value_kind"] != "boolean":
                        raise ValueError(
                            "Field presence must reference a boolean field"
                        )
                    if presence["width"] not in (1, f["width"]):
                        raise ValueError(
                            "Presence width must be scalar or match the field"
                        )
                    masks.append(
                        tuple(
                            presence["offset"] + (i if presence["width"] > 1 else 0)
                            for i in range(f["width"])
                        )
                    )
                divisor = first["normalization"]["divisor"]
                divisor = (
                    tuple(divisor)
                    if isinstance(divisor, list)
                    else (divisor,) * first["width"]
                )
                categories = (
                    len(first["vocabulary"])
                    if first["value_kind"] == "categorical"
                    else None
                )
                if (
                    categories is not None
                    and first["encoding"]
                    != "one_based_vocabulary_index_divided_by_divisor"
                ):
                    raise ValueError("Unsupported categorical encoding")
                descriptors.append(
                    TokenField(tuple(indices), tuple(masks), divisor, categories)
                )
                width += first["width"] * (
                    categories + 1 if categories is not None else 1
                )
            groups.append(
                TokenGroup(
                    tuple(entities),
                    tuple(presence_indices),
                    tuple(descriptors),
                    width,
                )
            )
        if sorted(covered) != list(range(size)):
            raise ValueError("Groups must cover canonical fields exactly once")
        object.__setattr__(self, "groups", tuple(groups))
        object.__setattr__(self, "size", size)


def weight_shapes(encoder, network):
    network = architecture(network)
    input_size, output_size = (
        encoder.encoder_spec()["flat_size"],
        encoder.action_spec()["size"],
    )
    result = {}

    def linear(prefix, source, target):
        result[prefix + ".weight"] = (target, source)
        result[prefix + ".bias"] = (target,)

    def norm(prefix, dim):
        result[prefix + ".weight"] = (dim,)
        result[prefix + ".bias"] = (dim,)

    if network["family"] == "mlp":
        width = input_size
        for index, target in enumerate(network["hidden"]):
            linear(f"trunk.layers.{index}", width, target)
            width = target
    else:
        width = network["dim"]
        for index, group in enumerate(TokenLayout(encoder.encoder_spec()).groups):
            linear(f"trunk.groups.{index}", group.width, width)
            result[f"trunk.groups.{index}.position"] = (len(group.entities), width)
        result["trunk.cls"] = (1, width)
        for index in range(network["layers"]):
            prefix = f"trunk.layers.{index}"
            for name in ("norm1", "norm2"):
                norm(prefix + "." + name, width)
            linear(prefix + ".qkv", width, 3 * width)
            linear(prefix + ".out", width, width)
            linear(prefix + ".ff1", width, network["ff"])
            linear(prefix + ".ff2", network["ff"], width)
        norm("trunk.norm", width)
    linear("pi", width, output_size)
    if sum(math.prod(shape) * 4 for shape in result.values()) > MAX_WEIGHT_BYTES:
        raise ValueError("Policy exceeds inference format size limit")
    return result


# Identities of encoders whose layout is fixed for their class (``static_layout``): computed once per process.
_STATIC_IDENTITIES = {}


def identities(encoder):
    static = getattr(type(encoder), "static_layout", None)
    if static is not None:
        key = (type(encoder), static)
        if key not in _STATIC_IDENTITIES:
            _STATIC_IDENTITIES[key] = _identities(encoder)
        return dict(_STATIC_IDENTITIES[key])
    return _identities(encoder)


def _identities(encoder):
    from boardcraft.registry import get_game

    spec, actions = encoder.encoder_spec(), encoder.action_spec()
    if spec["game_id"] != actions["game_id"]:
        raise ValueError("Encoder and actions belong to different games")
    return {
        "game_id": spec["game_id"],
        "rules_version": get_game(spec["game_id"]).rules_version,
        "observation_version": spec["observation_version"],
        "encoder_id": spec["encoder_id"],
        "encoder_version": spec["version"],
        "encoder_layout_hash": digest(spec),
        "action_id": actions["action_id"],
        "action_version": actions["version"],
        "action_layout_hash": digest(actions),
        "input_size": spec["flat_size"],
        "output_size": actions["size"],
    }
