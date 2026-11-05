"""Encoders with a fixed layout compute their identities once per process; the result is the same and private."""
from boardcraft.inference import spec
from boardcraft.registry import get_game


def test_static_layout_identities_are_computed_once_and_equal_the_full_computation(monkeypatch):
    encoder = get_game("dice_circuit").encoder()
    expected = spec._identities(encoder)
    first = spec.identities(encoder)
    assert first == expected
    calls = []
    monkeypatch.setattr(type(encoder), "encoder_spec", lambda self: calls.append(1) or {})
    again = spec.identities(get_game("dice_circuit").encoder())
    assert again == expected and calls == []
    # Callers get their own copy: changing one never changes the cached identities.
    again["input_size"] = -1
    assert spec.identities(encoder) == expected
