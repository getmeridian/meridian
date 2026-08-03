"""Reporter adapters for process/API clients."""

from __future__ import annotations

import sys
from typing import IO

from meridian.core.models import Event
from meridian.renderers import emit_jsonl


class JsonlReporter:
    """Reporter that writes core events as JSONL records."""

    def __init__(self, *, stream: IO[str] | None = None) -> None:
        self.stream = stream or sys.stderr

    def emit(self, event: Event) -> None:
        emit_jsonl(event, stream=self.stream)
