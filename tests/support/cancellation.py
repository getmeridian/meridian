"""In-memory cancellation token for tests."""

from __future__ import annotations

import threading


class CancellationFlag:
    """Thread-safe token satisfying ``meridian.core.cancellation.CancellationToken``."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()
