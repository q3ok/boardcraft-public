"""Optional saved versions of a model in the bot list: nested under the model, each still its own choice."""

from boardcraft.inference.catalog import nest_versions


def items():
    return [
        {"choice_id": "greedy", "display_name": "Greedy bot", "kind": "builtin", "reference": {}},
        {"choice_id": "m-final", "display_name": "Model", "kind": "model", "reference": {}},
        {"choice_id": "m-ckpt-3", "display_name": "Model save 3", "kind": "model", "reference": {}},
    ]


def test_versions_are_nested_and_hidden_from_the_top_level():
    listed = nest_versions(items(), {"m-final": {"versions": [{"choice_id": "m-final", "label": "final"},
                                                               {"choice_id": "m-ckpt-3", "label": "checkpoint 3"}],
                                                  "training": False}})
    assert [i["choice_id"] for i in listed] == ["greedy", "m-final"]
    assert listed[1]["versions"][1] == {"choice_id": "m-ckpt-3", "label": "checkpoint 3"}
    assert listed[1]["training"] is False


def test_invalid_or_missing_versions_leave_the_plain_list():
    plain = items()
    assert nest_versions(plain, None) == plain
    assert nest_versions(plain, {"m-final": {"versions": [{"choice_id": "unknown", "label": "x"}]}}) == plain
    assert nest_versions(plain, {"greedy": {"versions": [{"choice_id": "m-ckpt-3", "label": "x"}]}}) == plain
    assert nest_versions(plain, {"m-final": {"versions": [{"choice_id": "m-ckpt-3", "label": ""}]}}) == plain
    assert nest_versions(plain, {"m-final": {"versions": [], "training": "yes"}}) == plain
