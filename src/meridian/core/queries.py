"""Bounded integer options shared by HTTP query adapters and human CLI flags."""

from __future__ import annotations


def bounded_integer(value: str, name: str, lower: int, upper: int) -> int:
    """Parse one decimal integer, including Persian digits, with a plain field-specific error."""
    if not value.isdecimal() or len(value) > 16 or not lower <= int(value) <= upper:
        raise ValueError(f"{name} must be one integer between {lower} and {upper}.")
    return int(value)
