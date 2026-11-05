import pytest
from boardcraft.inference.catalog import RuntimeBotCatalog
from boardcraft.registry import get_game
from .test_bots import KINDS


@pytest.mark.parametrize("kind", KINDS)
def test_explicit_defaults_equal_omission(kind):
    catalog = RuntimeBotCatalog()
    schema = get_game("rentcraft").bot_settings_schema(kind)
    defaults = {k: v["default"] for k, v in schema["properties"].items()}
    assert catalog.pin_builtin("rentcraft", kind, defaults) == catalog.pin("rentcraft", kind)


def test_options_are_strict_and_change_identity():
    catalog = RuntimeBotCatalog()
    assert catalog.pin_builtin("rentcraft", "calculator", {"max_bid_factor": 1}) == catalog.pin("rentcraft", "calculator")
    assert (catalog.pin_builtin("rentcraft", "calculator", {"max_bid_factor": 2})
            == catalog.pin_builtin("rentcraft", "calculator", {"max_bid_factor": 2.0}))
    ref = catalog.pin_builtin("rentcraft", "calculator", {"min_reserve": 421})
    assert ref != catalog.pin("rentcraft", "calculator")
    for bad in [{"min_reserve": True}, {"min_reserve": -1}, {"go_increment": 40}, {"max_bid_factor": "2"}]:
        with pytest.raises(ValueError):
            catalog.pin_builtin("rentcraft", "calculator", bad)
    for kind in ("model:file", "hybrid:calculator", "../master"):
        with pytest.raises(ValueError):
            get_game("rentcraft").bot(kind, 9)


def test_sol_sets_accepts_short_bonus_schedules_and_optional_color_weights():
    module = get_game("rentcraft")
    module.bot("sol-sets", 1, options={"station_bonuses": [0, 30], "color_weights": []})
    for invalid in ([], [True], ["forty"]):
        with pytest.raises(ValueError):
            module.bot("sol-sets", 1, options={"station_bonuses": invalid})


@pytest.mark.parametrize("field", ["color_weights", "station_bonuses", "utility_bonuses"])
def test_review_signed_valuation_schedule_can_be_pinned_and_resolved(field):
    catalog = RuntimeBotCatalog()
    options = {field: [0, -25]}
    ref = catalog.pin_builtin("rentcraft", "sol-sets", options)
    assert catalog.resolve(ref, 77).save_state()["options"] == options

