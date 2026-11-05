"""User-supplied participant labels are visible text, never machine IDs."""

import pytest

from boardcraft.inference.catalog import display_name


@pytest.mark.parametrize("name", ["Żółw", "123", "A" * 128, "Player 🦉"])
def test_visible_user_names_are_preserved(name):
    assert display_name(name) == name


@pytest.mark.parametrize("name", ["", " ", " Player", "Player ", "A" * 129, "Player\nname", "Player\x00name"])
def test_invisible_or_unbounded_names_are_rejected(name):
    with pytest.raises(ValueError):
        display_name(name)
