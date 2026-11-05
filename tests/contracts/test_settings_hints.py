"""Form hints in settings schemas: labels of choices, suggested values and the place in a form."""
import pytest

from boardcraft.games.rentcraft.module import SETTINGS
from boardcraft.server.decision_clock import live_schema, live_settings
from boardcraft.settings import normalize_settings


def test_hints_are_accepted_and_do_not_change_values():
    assert normalize_settings(SETTINGS, {"max_rounds": 100}) == {"max_rounds": 100}
    assert SETTINGS["properties"]["trade_windows"]["enumLabels"]["payments"] == "on payments"
    orders = sorted(f["order"] for f in [*SETTINGS["properties"].values(), *live_schema("rentcraft")["properties"].values()])
    assert orders == list(range(1, 10))  # the nine options of the form, each in its own place


@pytest.mark.parametrize("hint", [
    {"presets": []},
    {"presets": [{"value": -1, "label": "minus"}]},
    {"presets": [{"value": 1}]},
    {"enumLabels": {"a": "A"}},
    {"order": "1"},
])
def test_bad_hints_are_rejected(hint):
    schema = {"type": "object", "additionalProperties": False,
              "properties": {"n": {"type": "integer", "minimum": 0, "default": 1, **hint}}}
    with pytest.raises(ValueError):
        normalize_settings(schema, {})


def test_bot_speed_is_watchable_with_people_and_instant_with_bots_only():
    assert live_settings("rentcraft", [{"kind": "human"}, {"kind": "bot"}], {})["bot_delay"] == .6
    assert live_settings("rentcraft", [{"kind": "bot"}, {"kind": "bot"}], {})["bot_delay"] == 0
    agent = {"kind": "human", "control_kind": "agent"}
    assert live_settings("rentcraft", [agent, {"kind": "bot"}], {})["bot_delay"] == 0
    presets = live_schema("rentcraft")["properties"]["agent_timeout"]["presets"]
    assert [p["label"] for p in presets] == ["30 s", "60 s", "120 s", "no limit"]
