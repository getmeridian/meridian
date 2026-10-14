"""Terminal-output helpers for CLI tests."""

from __future__ import annotations

from rich.text import Text


def unstyle(output: str) -> str:
    """Drop ANSI style codes; CI forces color, which splits words like ``--name``."""
    return Text.from_ansi(output).plain
