"""Shared Engine error type for local API and CLI adapters."""

from __future__ import annotations

from meridian.core.models import ErrorCategory


class EngineError(RuntimeError):
    """Typed Engine error for CLI/API adapters to render."""

    def __init__(self, message: str, *, hint: str = "", category: ErrorCategory = "user") -> None:
        super().__init__(message)
        self.hint = hint
        self.category = category
