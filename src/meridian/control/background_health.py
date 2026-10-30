"""Thread-safe, process-local background task evidence and secret-free warnings."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from meridian.control.models import BackgroundErrorCode, BackgroundTaskName, BackgroundTaskState, ServiceHealth

WARNING_INTERVAL_SECONDS = 300
_LOGGER = logging.getLogger(__name__)


class TaskHealth:
    def __init__(
        self,
        name: BackgroundTaskName,
        error_code: BackgroundErrorCode,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._clock = clock
        self._error_code = error_code
        self._lock = threading.Lock()
        self._state = BackgroundTaskState(name=name)
        self._last_warning: float | None = None

    def _stamp(self) -> str:
        return datetime.fromtimestamp(self._clock(), UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    def attempt(self) -> None:
        with self._lock:
            self._state = self._state.model_copy(update={"last_attempt_at": self._stamp()})

    def succeeded(self, *, clear_error: bool = True) -> None:
        with self._lock:
            self._state = self._state.model_copy(
                update={
                    "last_success_at": self._stamp(),
                    "error_code": None if clear_error else self._state.error_code,
                }
            )

    def failed(self) -> None:
        with self._lock:
            self._state = self._state.model_copy(update={"error_code": self._error_code})
            now = self._clock()
            warn = self._last_warning is None or now - self._last_warning >= WARNING_INTERVAL_SECONDS
            if warn:
                self._last_warning = now
        if warn:
            _LOGGER.warning(
                "Background task %s failed (%s); the next attempt will retry", self._state.name, self._error_code
            )

    def clear_error(self) -> None:
        """Clear an obsolete failure without recording a task attempt or success."""
        with self._lock:
            self._state = self._state.model_copy(update={"error_code": None})

    def run(self, work: Callable[[], object]) -> bool:
        self.attempt()
        try:
            work()
        except Exception:  # noqa: BLE001 - background retries, with safe observable failure evidence
            self.failed()
            return False
        self.succeeded()
        return True

    def state(self) -> BackgroundTaskState:
        with self._lock:
            return self._state.model_copy()


class BackgroundHealth:
    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self.enforcement = TaskHealth("enforcement", "enforcement_failed", clock=clock)
        self.blob_mirror = TaskHealth("blob_mirror", "blob_mirror_failed", clock=clock)

    def health(self, *, version: str, fleet_id: str, fleet_epoch: int) -> ServiceHealth:
        tasks = [self.enforcement.state(), self.blob_mirror.state()]
        return ServiceHealth(
            status="degraded" if any(task.error_code for task in tasks) else "ok",
            version=version,
            fleet_id=fleet_id,
            fleet_epoch=fleet_epoch,
            background_tasks=tasks,
        )
