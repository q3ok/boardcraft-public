"""Optional structured decision annotations preserve absent-field record bytes."""
from dataclasses import replace
import pytest
from boardcraft.contracts import Action, BotChoice, GameConfig
from boardcraft.registry import get_game
from boardcraft.play.records import _decode, record_data
from boardcraft.play.runner import run_game


def test_bot_choice_meta_is_optional_validated_and_detached():
    action = Action("roll", "roll", {})
    assert BotChoice(action, "Choose").meta is None
    source = {"probabilities": [{"action": "roll", "probability": 1.0}], "value": 0.5, "reason": "Example"}
    choice = BotChoice(action, "Choose", meta=source)
    source["value"] = 8
    assert choice.meta["value"] == 0.5
    for invalid in [[], "text", {"value": float("nan")}, {"probabilities": "bad"},
                    {"probabilities": [{"action": "roll", "probability": 2}]},
                    {"value": True}, {"reason": []}, {"draft": {}}]:
        with pytest.raises((ValueError, TypeError)):
            BotChoice(action, "Choose", meta=invalid)


def test_record_meta_roundtrips_and_runner_retains_explicit_annotations():
    module = get_game("rentcraft")
    class Annotated:
        def __init__(self, seed): self.bot = module.bot("random", seed)
        def choose(self, observation, legal):
            return replace(self.bot.choose(observation, legal), meta={"value": 0.25})
    record = run_game(module, GameConfig("rentcraft", "base", 2, {"max_rounds": 1}), 19,
                      [Annotated(1), Annotated(2)])
    data = record_data(record)
    assert all(op["meta"] == {"value": 0.25} for op in data["operations"])
    assert _decode(data) == record
    plain = replace(record, operations=tuple(replace(op, meta=None) for op in record.operations))
    assert all("meta" not in op for op in record_data(plain)["operations"])
    data["operations"][0]["meta"] = {"probabilities": False}
    with pytest.raises(ValueError): _decode(data)


def test_legacy_decision_record_retains_explicit_meta():
    module = get_game("dice_circuit")
    class Annotated:
        def __init__(self): self.bot = module.bot("random", 1)
        def choose(self, observation, legal):
            return replace(self.bot.choose(observation, legal), meta={"reason": "Test annotation"})
    record = run_game(module, GameConfig("dice_circuit", "base", 1), 19, [Annotated()])
    data = record_data(record)
    assert all(step.get("meta") == {"reason": "Test annotation"} for step in data["decisions"])
    assert _decode(data) == record
