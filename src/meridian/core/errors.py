"""Shared exception hierarchy for all Meridian subsystems.

Every user-facing subsystem error inherits ``MeridianError``.  CLI adapters
catch it to render human-readable output via ``console.fail()``.  Engine/API
adapters catch it to build typed JSON error responses via ``to_error_model()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from meridian.core.models import ErrorCategory

if TYPE_CHECKING:
    from meridian.core.models import MeridianError as MeridianErrorModel

_EXIT_CODES: dict[str, int] = {"user": 2, "system": 3, "bug": 1, "cancelled": 130}


class MeridianError(Exception):
    """Base for all typed Meridian exceptions.

    Subsystem errors inherit this.  CLI adapters catch it to render
    human-readable output.  Engine/API adapters catch it to build
    typed error responses.  The ``to_error_model()`` method serializes
    to the JSON-safe ``MeridianError`` Pydantic model.
    """

    def __init__(
        self,
        message: str,
        *,
        hint: str = "",
        category: ErrorCategory = "system",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.hint = hint
        self.category = category
        self.retryable = retryable

    @property
    def exit_code(self) -> int:
        return _EXIT_CODES.get(self.category, 1)

    def to_error_model(self) -> MeridianErrorModel:
        from meridian.core.models import MeridianError as MeridianErrorModel

        return MeridianErrorModel(
            code=type(self).__name__,
            category=self.category,
            message=str(self),
            hint=self.hint,
            retryable=self.retryable,
            exit_code=self.exit_code,
        )


class EngineError(MeridianError):
    """Typed Engine error for CLI/API adapters to render.

    Lives in core so that both adapters and engine can import it
    without creating circular dependencies.
    """

    def __init__(self, message: str, *, hint: str = "", category: ErrorCategory = "user") -> None:
        super().__init__(message, hint=hint, category=category)


class ProvisioningError(MeridianError):
    """SSH provisioner pipeline failure (step failed, container unhealthy, etc.)."""

    def __init__(self, message: str, *, hint: str = "", category: ErrorCategory = "system") -> None:
        super().__init__(message, hint=hint, category=category)


class PanelSetupError(MeridianError):
    """Remnawave panel API configuration failure (register, token, node, profile)."""

    def __init__(self, message: str, *, hint: str = "", category: ErrorCategory = "system") -> None:
        super().__init__(message, hint=hint, category=category)
