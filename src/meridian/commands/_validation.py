"""Command input validation adapters."""

from __future__ import annotations

from typing import TypeVar

from pydantic import ValidationError

from meridian.console import fail
from meridian.core.models import CoreModel
from meridian.core.validation import wrap_validation_error

T = TypeVar("T", bound=CoreModel)


def validate_command_input(model: type[T], message: str, **fields: object) -> T:
    """Build a core request model and render validation failures as CLI errors."""
    try:
        return model(**fields)
    except ValidationError as exc:
        error = wrap_validation_error(message, exc)
        fail(str(error), hint=error.hint, hint_type="user")
