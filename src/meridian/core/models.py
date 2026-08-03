"""Stable meridian-core output, error, and event models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from meridian.core.events import CoreEventType

OutputStatus = Literal["ok", "changed", "no_changes", "failed", "cancelled"]
ErrorCategory = Literal["user", "system", "bug", "cancelled"]
EventLevel = Literal["debug", "info", "warning", "error"]
OutputSchema = Literal["meridian.output/v1"]
EventSchema = Literal["meridian.event/v1"]


class CoreModel(BaseModel):
    """Base class for externally visible meridian-core contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, validate_by_alias=True, validate_by_name=True)


class Summary(CoreModel):
    """Structured summary shown in JSON envelopes and UI dashboards."""

    text: str
    changed: bool = False
    counts: dict[str, int] = Field(default_factory=dict)


class MeridianError(CoreModel):
    """Structured error safe for JSON/API clients."""

    code: str
    category: ErrorCategory
    message: str
    hint: str = ""
    retryable: bool = False
    exit_code: int = 1
    details: dict[str, Any] = Field(default_factory=dict)
    cause: dict[str, Any] = Field(default_factory=dict)


class OutputEnvelope(CoreModel):
    """One-shot JSON result envelope for command/process APIs."""

    schema_version: OutputSchema = Field(default="meridian.output/v1", alias="schema")
    meridian_version: str
    command: str
    operation_id: str
    started_at: str
    duration_ms: int
    status: OutputStatus
    exit_code: int
    summary: Summary
    data: dict[str, Any] = Field(default_factory=dict)
    warnings: list[MeridianError] = Field(default_factory=list)
    errors: list[MeridianError] = Field(default_factory=list)


class ResourceRef(CoreModel):
    """Resource identifier carried by events."""

    kind: str
    id: str
    name: str = ""


class Event(CoreModel):
    """JSONL event model for long-running operations."""

    schema_version: EventSchema = Field(default="meridian.event/v1", alias="schema")
    operation_id: str
    seq: int
    time: str
    level: EventLevel
    type: CoreEventType
    phase: str = ""
    resource: ResourceRef | None = None
    message: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
