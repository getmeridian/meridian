"""Output envelope builders and JSON serialization helpers."""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from meridian import __version__
from meridian.core.events import CoreEventType
from meridian.core.models import (
    Event,
    EventLevel,
    EventSchema,
    MeridianError,
    OutputEnvelope,
    OutputSchema,
    OutputStatus,
    ResourceRef,
    Summary,
)
from meridian.core.plan import build_plan_result
from meridian.core.redaction import redact
from meridian.core.serde import to_plain

OUTPUT_SCHEMA: OutputSchema = "meridian.output/v1"
EVENT_SCHEMA: EventSchema = "meridian.event/v1"


def now_iso() -> str:
    """Current UTC timestamp in ISO-8601 form."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_operation_id() -> str:
    """Generate an opaque operation id."""
    return uuid.uuid4().hex


class OperationTimer:
    """Small helper for consistent envelope timing."""

    def __init__(self, *, started_at: str | None = None, operation_id: str | None = None) -> None:
        self.started_at = started_at or now_iso()
        self.operation_id = operation_id or new_operation_id()
        self._started = time.monotonic()

    def duration_ms(self) -> int:
        return int((time.monotonic() - self._started) * 1000)


class OperationContext:
    """Shared operation metadata for final envelopes and event streams."""

    def __init__(self, *, operation_id: str | None = None, started_at: str | None = None) -> None:
        self.timer = OperationTimer(started_at=started_at, operation_id=operation_id)
        self.events = EventStream(operation_id=self.timer.operation_id)

    @property
    def operation_id(self) -> str:
        return self.timer.operation_id


def envelope(
    *,
    command: str,
    data: dict[str, Any] | None = None,
    summary: Summary | str = "",
    status: OutputStatus = "ok",
    exit_code: int = 0,
    warnings: list[MeridianError] | None = None,
    errors: list[MeridianError] | None = None,
    timer: OperationTimer | None = None,
) -> OutputEnvelope:
    """Build a standard output envelope."""
    timer = timer or OperationTimer()
    summary_obj = summary if isinstance(summary, Summary) else Summary(text=summary, changed=status == "changed")
    return OutputEnvelope(
        schema=OUTPUT_SCHEMA,
        meridian_version=__version__,
        command=command,
        operation_id=timer.operation_id,
        started_at=timer.started_at,
        duration_ms=timer.duration_ms(),
        status=status,
        exit_code=exit_code,
        summary=summary_obj,
        data=data or {},
        warnings=warnings or [],
        errors=errors or [],
    )


def command_envelope(
    *,
    command: str,
    data: dict[str, Any] | None = None,
    summary: Summary | str = "",
    status: OutputStatus = "ok",
    exit_code: int = 0,
    warnings: list[MeridianError] | None = None,
    errors: list[MeridianError] | None = None,
    timer: OperationTimer | None = None,
) -> OutputEnvelope:
    """Build and validate an advertised command-specific output envelope."""
    payload = envelope(
        command=command,
        data=data,
        summary=summary,
        status=status,
        exit_code=exit_code,
        warnings=warnings,
        errors=errors,
        timer=timer,
    )
    from meridian.core.schema import validate_command_envelope

    return validate_command_envelope(payload)


def json_dumps(value: Any) -> str:
    """Return a redacted, formatted JSON string for a core model or plain value."""
    return json.dumps(redact(to_plain(value)), indent=2, sort_keys=True)


def jsonl_dumps(value: Any) -> str:
    """Return a redacted, compact JSON string for one JSONL record."""
    return json.dumps(redact(to_plain(value)), sort_keys=True, separators=(",", ":"))


def plan_payload(plan: Any, *, exit_code: int) -> dict[str, Any]:
    """Return the stable plan data payload used under output envelopes."""
    return build_plan_result(plan, exit_code=exit_code).to_data()


class EventStream:
    """Monotonic JSONL event builder for long-running operations."""

    def __init__(self, *, operation_id: str | None = None) -> None:
        self.operation_id = operation_id or new_operation_id()
        self._seq = 0

    def event(
        self,
        event_type: CoreEventType,
        *,
        level: EventLevel = "info",
        phase: str = "",
        resource: ResourceRef | None = None,
        message: str = "",
        data: dict[str, Any] | None = None,
    ) -> Event:
        """Build the next event without writing it."""
        self._seq += 1
        return Event(
            schema=EVENT_SCHEMA,
            operation_id=self.operation_id,
            seq=self._seq,
            time=now_iso(),
            level=level,
            type=event_type,
            phase=phase,
            resource=resource,
            message=message,
            data=data or {},
        )

    def record(
        self,
        event_type: CoreEventType,
        *,
        level: EventLevel = "info",
        phase: str = "",
        resource: ResourceRef | None = None,
        message: str = "",
        data: dict[str, Any] | None = None,
    ) -> str:
        """Build the next event and return it serialized as one JSONL record."""
        return jsonl_dumps(
            self.event(
                event_type,
                level=level,
                phase=phase,
                resource=resource,
                message=message,
                data=data,
            )
        )
