"""Serialization helpers for meridian-core contracts."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel


def to_plain(value: Any) -> Any:
    """Convert dataclasses/enums/containers to JSON-serializable objects."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True)
    if is_dataclass(value):
        return {field.name: to_plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): to_plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [to_plain(v) for v in value]
    return value
