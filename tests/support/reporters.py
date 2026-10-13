"""In-memory reporter for tests."""

from __future__ import annotations

from meridian.core.models import Event


class CaptureReporter:
    """Reporter that keeps every emitted event in order."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    def emit(self, event: Event) -> None:
        self.events.append(event)
