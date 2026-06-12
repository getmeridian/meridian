"""Typed error base for Engine and adapter boundaries."""

from __future__ import annotations

from meridian.core.models import ErrorCategory


class EngineError(RuntimeError):
    """Typed Engine error for CLI/API adapters to render.

    Lives in core so that both adapters and engine can import it
    without creating circular dependencies.
    """

    def __init__(self, message: str, *, hint: str = "", category: ErrorCategory = "user") -> None:
        super().__init__(message)
        self.hint = hint
        self.category = category
