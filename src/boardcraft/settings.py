"""Strict normalization for the closed JSON Schema subset used by play settings."""
from collections.abc import Mapping
import math

from .contracts import _copy_json

_TYPES = {"object", "array", "string", "boolean", "integer", "number", "null"}
_KEYWORDS = {
    "type", "properties", "additionalProperties", "items", "enum", "default",
    "minimum", "maximum", "minItems", "maxItems", "title", "description",
    # presentation hints for a settings form; they never change what a value may be
    "enumLabels", "presets", "order",
}


def _same(first, second):
    if type(first) is not type(second):
        return False
    if isinstance(first, dict):
        return first.keys() == second.keys() and all(_same(first[k], second[k]) for k in first)
    if isinstance(first, list):
        return len(first) == len(second) and all(_same(a, b) for a, b in zip(first, second))
    return first == second


def _types(schema):
    types = schema.get("type")
    if isinstance(types, str):
        types = [types]
    if (not isinstance(types, list) or not types
            or any(not isinstance(item, str) or item not in _TYPES for item in types)
            or len(set(types)) != len(types)):
        raise ValueError("Settings schema requires explicit supported types")
    return types


def _enum_values(schema):
    without_enum = {key: value for key, value in schema.items() if key != "enum"}
    return [_value(without_enum, _copy_json(choice)) for choice in schema["enum"]]


def _schema(schema):
    if not isinstance(schema, dict) or set(schema) - _KEYWORDS:
        raise ValueError("Unsupported settings schema")
    types = _types(schema)
    for annotation in ("title", "description"):
        if annotation in schema and not isinstance(schema[annotation], str):
            raise ValueError("Settings labels must be strings")
    if "object" in types:
        properties = schema.get("properties")
        if (schema.get("additionalProperties") is not False
                or not isinstance(properties, dict)
                or any(not isinstance(key, str) for key in properties)):
            raise ValueError("Settings objects must declare closed properties")
        for item in properties.values():
            _schema(item)
    elif "properties" in schema or "additionalProperties" in schema:
        raise ValueError("Object constraints require an object type")
    if "array" in types:
        _schema(schema.get("items"))
    elif any(key in schema for key in ("items", "minItems", "maxItems")):
        raise ValueError("Array constraints require an array type")
    for key in ("minItems", "maxItems"):
        if key in schema and (type(schema[key]) is not int or schema[key] < 0):
            raise ValueError("Array bounds must be nonnegative integers")
    for key in ("minimum", "maximum"):
        if key in schema and (not {"number", "integer"}.intersection(types)
                or type(schema[key]) not in (int, float)
                or type(schema[key]) is float and not math.isfinite(schema[key])):
            raise ValueError("Numeric bounds must be finite numbers")
    for low, high in (("minimum", "maximum"), ("minItems", "maxItems")):
        if low in schema and high in schema and schema[low] > schema[high]:
            raise ValueError("Settings bounds are reversed")
    if "enum" in schema:
        choices = schema["enum"]
        if not isinstance(choices, list) or not choices:
            raise ValueError("Settings enum must be a nonempty array")
        choices = _enum_values(schema)
        for index, choice in enumerate(choices):
            if any(_same(choice, previous) for previous in choices[:index]):
                raise ValueError("Settings enum contains duplicate choices")
    if "default" in schema:
        _value(schema, _copy_json(schema["default"]))
    _hints(schema)


def _hints(schema):
    """Form hints: labels of enum choices, a short list of suggested values with labels, and the place in a form."""
    labels = schema.get("enumLabels")
    if labels is not None and (not isinstance(labels, dict) or "enum" not in schema
                               or any(not isinstance(k, str) or not isinstance(v, str) for k, v in labels.items())):
        raise ValueError("Settings enumLabels must map enum choices to strings")
    presets = schema.get("presets")
    if presets is not None:
        if not isinstance(presets, list) or not presets:
            raise ValueError("Settings presets must be a nonempty array")
        for preset in presets:
            if (not isinstance(preset, dict) or set(preset) != {"value", "label"}
                    or not isinstance(preset["label"], str)):
                raise ValueError("Each settings preset needs a value and a label")
            _value(schema, _copy_json(preset["value"]))
    if "order" in schema and type(schema["order"]) is not int:
        raise ValueError("Settings order must be an integer")


def _value(schema, value):
    types = _types(schema)
    actual = ("null" if value is None else "boolean" if type(value) is bool
              else "integer" if type(value) is int else "number" if type(value) is float
              else "string" if type(value) is str else "array" if type(value) is list
              else "object" if type(value) is dict else None)
    if actual not in types and not (actual == "integer" and "number" in types):
        raise ValueError("Settings value has an invalid type")
    if actual in ("integer", "number"):
        if "number" in types and "integer" not in types:
            try:
                numeric = float(value)
            except OverflowError:
                raise ValueError("Settings number is outside the finite range") from None
            if actual == "integer" and numeric != value:
                raise ValueError("Settings number cannot be represented exactly")
            value = 0.0 if numeric == 0 else numeric
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Settings numbers must be finite")
        if ("minimum" in schema and value < schema["minimum"]
                or "maximum" in schema and value > schema["maximum"]):
            raise ValueError("Settings number is outside its bounds")
    if actual == "array":
        if len(value) < schema.get("minItems", 0) or ("maxItems" in schema and len(value) > schema["maxItems"]):
            raise ValueError("Settings array is outside its bounds")
        value = [_value(schema["items"], item) for item in value]
    if actual == "object":
        properties = schema["properties"]
        if set(value) - properties.keys():
            raise ValueError("Unknown settings field")
        result = {}
        for key in sorted(properties):
            item = properties[key]
            if key in value or "default" in item:
                result[key] = _value(item, value[key] if key in value else _copy_json(item["default"]))
        value = result
    if "enum" in schema and not any(_same(value, choice) for choice in _enum_values(schema)):
        raise ValueError("Settings value is not an enum choice")
    return value


def normalize_settings(schema: dict, values: Mapping) -> dict:
    """Return detached, sorted overrides; validate every declared default as well."""
    _schema(schema)
    if schema.get("type") != "object":
        raise ValueError("Settings schema must be a closed object")
    if not isinstance(values, Mapping):
        raise ValueError("Settings must be an object")
    effective = _value(schema, _copy_json(values))
    defaults = _value(schema, {})
    return {key: value for key, value in effective.items()
            if key not in defaults or not _same(value, defaults[key])}
