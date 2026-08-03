"""Reporter primitives for meridian-core progress events."""

from __future__ import annotations

from typing import Protocol

from meridian.core.events import CoreEventType
from meridian.core.models import Event, EventLevel, ResourceRef
from meridian.core.output import OperationContext


class Reporter(Protocol):
    """Sink for typed core progress events."""

    def emit(self, event: Event) -> None: ...


class NoopReporter:
    """Reporter that intentionally drops events."""

    def emit(self, event: Event) -> None:
        return None


class CaptureReporter:
    """In-memory reporter for tests and embedded clients."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    def emit(self, event: Event) -> None:
        self.events.append(event)


def emit_event(
    reporter: Reporter,
    operation: OperationContext,
    event_type: CoreEventType,
    *,
    level: EventLevel = "info",
    phase: str = "",
    resource: ResourceRef | None = None,
    message: str = "",
    data: dict[str, object] | None = None,
) -> Event:
    """Create and emit one operation-scoped event."""
    event = operation.events.event(
        event_type,
        level=level,
        phase=phase,
        resource=resource,
        message=message,
        data=data or {},
    )
    reporter.emit(event)
    return event
