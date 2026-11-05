"""Bounded visible participant names, independent of machine identifiers."""


def display_name(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 128
            or not value.isprintable() or value != value.strip()):
        raise ValueError("Invalid participant display name")
    return value
