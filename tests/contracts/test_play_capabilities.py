"""Play-only games need no tensor contract, and settings have one strict schema."""
from dataclasses import dataclass, fields
from copy import deepcopy
import importlib

import pytest

from boardcraft import contracts
from boardcraft.registry import get_game


def settings_api():
    assert importlib.util.find_spec("boardcraft.settings"), "Settings normalization is missing"
    return importlib.import_module("boardcraft.settings")


def schema():
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "enabled": {"type": "boolean", "default": True},
            "limit": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
            "ratio": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5},
            "mode": {"type": "string", "enum": ["steady", "quick"], "default": "steady"},
            "levels": {"type": "array", "minItems": 1, "maxItems": 2,
                       "items": {"type": "integer", "minimum": 0, "maximum": 3}, "default": [1]},
            "ceiling": {"type": ["integer", "null"], "minimum": 1, "default": None},
            "nested": {"type": "object", "additionalProperties": False,
                       "properties": {"flag": {"type": "boolean", "default": False}},
                       "default": {"flag": False}},
        },
    }


@pytest.mark.parametrize("number_type", ["number", ["number", "null"]])
def test_number_settings_have_one_finite_numeric_representation(number_type):
    normalize = settings_api().normalize_settings
    definition = {"type": "object", "additionalProperties": False, "properties": {
        "bid_factor": {"type": number_type, "default": 1.0}}}
    assert normalize(definition, {"bid_factor": 1}) == {}
    assert type(normalize(definition, {"bid_factor": 2})["bid_factor"]) is float
    assert repr(normalize(definition, {"bid_factor": -0.0})["bid_factor"]) == "0.0"
    for invalid in (True, 10**400, 2**54 + 1):
        with pytest.raises(ValueError):
            normalize(definition, {"bid_factor": invalid})


def test_strict_settings():
    normalize = settings_api().normalize_settings
    assert normalize(schema(), {}) == {}
    values = {"mode": "quick", "enabled": True, "limit": 4, "levels": [2], "nested": {"flag": True}}
    result = normalize(schema(), values)
    assert result == {"levels": [2], "limit": 4, "mode": "quick", "nested": {"flag": True}}
    assert list(result) == ["levels", "limit", "mode", "nested"]
    values["levels"].append(3)
    assert result["levels"] == [2]
    for invalid in (
        {"limit": True}, {"limit": 2.0}, {"enabled": 1}, {"ratio": True},
        {"unknown": 1}, {"ratio": float("nan")}, {"ratio": float("inf")},
        {"limit": 0}, {"limit": 6}, {"mode": "unknown"},
        {"levels": []}, {"levels": [1, 2, 3]}, {"levels": [True]},
        {"levels": [4]}, {"levels": (1,)}, {"nested": {"extra": False}},
        {"ceiling": False}, {"ceiling": 0}, {1: "bad"},
        {"limit": 10 ** 400},
    ):
        with pytest.raises((TypeError, ValueError)):
            normalize(schema(), invalid)
    assert normalize(schema(), {"ceiling": None, "nested": {}}) == {}

    for invalid in ([], None, "settings"):
        with pytest.raises((TypeError, ValueError)):
            normalize(schema(), invalid)


@pytest.mark.parametrize("definition", [
    {"type": "boolean", "enum": [True], "default": True},
    {"type": "integer", "enum": [1], "default": 1},
    {"type": "number", "enum": [1], "default": 1},
])
def test_settings_enums_do_not_equate_booleans_and_numbers(definition):
    normalize = settings_api().normalize_settings
    definition = {"type": "object", "additionalProperties": False,
                  "properties": {"value": definition}}
    with pytest.raises((TypeError, ValueError)):
        normalize(definition, {"value": 1 if definition["properties"]["value"]["type"] == "boolean" else True})


@pytest.mark.parametrize("default", [True, 0, 7, float("nan"), object()])
def test_settings_reject_unsafe_defaults(default):
    normalize = settings_api().normalize_settings
    invalid = {"type": "object", "additionalProperties": False,
               "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 5, "default": default}}}
    with pytest.raises((TypeError, ValueError)):
        normalize(invalid, {})


def test_settings_enum_is_strict_even_when_both_json_types_are_allowed():
    normalize = settings_api().normalize_settings
    definition = {"type": "object", "additionalProperties": False, "properties": {
        "value": {"type": ["integer", "boolean"], "enum": [1], "default": 1},
    }}
    with pytest.raises(ValueError):
        normalize(definition, {"value": True})


@pytest.mark.parametrize("as_array", [False, True])
def test_settings_object_enum_and_default_expand_child_defaults_consistently(as_array):
    normalize = settings_api().normalize_settings
    profile = {"type": "object", "additionalProperties": False,
               "properties": {"level": {"type": "integer", "default": 1}}}
    default, expanded, alternative, invalid, wrong_type = {}, {"level": 1}, {"level": 2}, {"level": 3}, {"level": True}
    if as_array:
        profile = {"type": "array", "items": profile}
        default, expanded, alternative, invalid, wrong_type = [default], [expanded], [alternative], [invalid], [wrong_type]
    profile.update(enum=[deepcopy(default)], default=deepcopy(default))
    definition = {"type": "object", "additionalProperties": False,
                  "properties": {"profile": profile}}
    original = deepcopy(definition)
    for values in ({}, {"profile": default}, {"profile": expanded}):
        result = normalize(definition, values)
        assert result == {} and normalize(definition, result) == result
    assert definition == original

    profile["enum"].append(deepcopy(alternative))
    original = deepcopy(definition)
    expected = {"profile": alternative}
    result = normalize(definition, expected)
    assert result == expected and normalize(definition, result) == result
    for value in (invalid, wrong_type):
        with pytest.raises(ValueError):
            normalize(definition, {"profile": value})
    assert definition == original


@pytest.mark.parametrize("as_array", [False, True])
def test_settings_reject_enum_duplicates_after_child_default_expansion(as_array):
    normalize = settings_api().normalize_settings
    profile = {"type": "object", "additionalProperties": False,
               "properties": {"level": {"type": "integer", "default": 1}}}
    choices = [{}, {"level": 1}]
    if as_array:
        profile = {"type": "array", "items": profile}
        choices = [[choice] for choice in choices]
    profile["enum"] = choices
    definition = {"type": "object", "additionalProperties": False,
                  "properties": {"profile": profile}}
    original = deepcopy(definition)
    with pytest.raises(ValueError):
        normalize(definition, {})
    assert definition == original


@pytest.mark.parametrize("definition", [
    {"type": "object", "properties": {}},
    {"type": "object", "properties": {}, "additionalProperties": True},
    {"type": "object", "properties": {}, "additionalProperties": False, "pattern": "*"},
    {"type": "object", "properties": {"x": {"type": "array", "default": []}}, "additionalProperties": False},
    {"type": "object", "properties": {"x": {"type": "number", "minimum": float("nan")}}, "additionalProperties": False},
    {"type": "object", "properties": {"x": {"type": "integer", "minimum": 3, "maximum": 1}}, "additionalProperties": False},
    {"type": "object", "properties": {"x": {"type": "integer", "enum": [True]}}, "additionalProperties": False},
])
def test_settings_reject_open_or_unsupported_schemas(definition):
    with pytest.raises(ValueError):
        settings_api().normalize_settings(definition, {})


def test_outcome_protocol_keeps_dice_results_and_accepts_other_games():
    assert hasattr(contracts, "GameOutcome"), "Structural game outcome is missing"

    @dataclass(frozen=True)
    class Outcome:
        scores: tuple[int, ...]
        placements: tuple[int, ...]
        reason: str

    assert isinstance(Outcome((7, 3), (1, 2), "completed"), contracts.GameOutcome)
    result = contracts.GameResult((7,), ((1, 2),), (4,), (1,), "completed")
    assert isinstance(result, contracts.GameOutcome)
    assert tuple(field.name for field in fields(result)) == (
        "scores", "area_scores", "emblem_scores", "placements", "reason"
    )


def test_dice_capabilities_and_empty_settings_preserve_play_contract():
    module = get_game("dice_circuit")
    assert hasattr(module.info(), "capabilities"), "Explicit play capabilities are missing"
    assert module.info().capabilities == contracts.PlayCapabilities(True, False, False, False)
    assert module.board_spec() == module.sheet_spec()
    assert module.settings_schema() == {"type": "object", "properties": {}, "additionalProperties": False}
    assert module.bot_settings_schema("random") == module.settings_schema()
    assert module.bot("random", 1, options={})
    with pytest.raises(ValueError):
        module.bot("random", 1, options={"extra": True})
    with pytest.raises(ValueError):
        module.bot_settings_schema("unknown")


def test_capabilities_require_real_booleans():
    assert hasattr(contracts, "PlayCapabilities"), "Explicit play capabilities are missing"
    with pytest.raises((TypeError, ValueError)):
        contracts.PlayCapabilities(1, False, False, False)
