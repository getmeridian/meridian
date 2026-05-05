"""Helpers for turning Pydantic validation details into user-facing hints."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError


class CoreInputError(ValueError):
    """Readable validation error suitable for CLI/API adapters."""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


def validation_error_hint(exc: ValidationError) -> str:
    """Format Pydantic validation errors as compact field-specific hints."""
    lines: list[str] = []
    for error in exc.errors():
        field = _format_location(error.get("loc", ()))
        message = _format_message(error)
        lines.append(f"{field}: {message}" if field else message)
    return "\n".join(f"- {line}" for line in lines)


def wrap_validation_error(message: str, exc: ValidationError) -> CoreInputError:
    """Wrap a Pydantic validation error with a concise summary and hint."""
    return CoreInputError(message, hint=validation_error_hint(exc))


def _format_location(location: Any) -> str:
    if not location:
        return ""
    if not isinstance(location, tuple):
        return str(location)
    return ".".join(str(part) for part in location)


def _format_message(error: Mapping[str, Any]) -> str:
    if error.get("type") == "value_error":
        ctx_error = error.get("ctx", {}).get("error")
        if ctx_error:
            return str(ctx_error)
    if error.get("type") == "extra_forbidden":
        return "Unknown field. Remove it or check the field name."
    if error.get("type") == "missing":
        return "Field is required."
    return str(error.get("msg", "Invalid value."))
