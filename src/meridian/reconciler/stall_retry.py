"""Retry read-only work through a stalled SSH connection.

A dropped or stalled connection fails one command with a timeout or an SSH
transport error, and ``ServerConnection`` drops its control socket so the next
command dials afresh. Observations and other self-checking steps can therefore
simply run again after a pause. Mutations never come through here: an uncertain
mutation raises ``UnknownResourceOutcome`` and is observed before any retry.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

from meridian.core.errors import MeridianError
from meridian.core.events import WARNING
from meridian.core.models import ResourceRef
from meridian.core.output import OperationContext
from meridian.core.reporters import Reporter, emit_event
from meridian.reconciler.resources import UnknownResourceOutcome

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Pauses before each retry; together with SSH's connect timeout they ride out an outage of about a minute.
STALL_DELAYS: tuple[float, ...] = (2.0, 5.0, 10.0, 20.0)
CONNECTION_STALLED = "connection_stalled"

StallNotice = Callable[[int, float, MeridianError], None]
"""Called before each retry with the attempt that failed, the pause, and its error."""


def is_transient(exc: BaseException) -> bool:
    """A retryable system failure (lost evidence, a stalled write), never an uncertain mutation."""
    return (
        isinstance(exc, MeridianError)
        and exc.retryable
        and exc.category == "system"
        and not isinstance(exc, UnknownResourceOutcome)
    )


class StallRetry:
    """Run a read-only or self-checking call again while it fails transiently.

    One outage that outlasts every pause ends retries for this instance, so a
    server that is really gone fails each later call at once instead of
    waiting out the whole schedule again.
    """

    def __init__(
        self,
        *,
        delays: tuple[float, ...] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        stop: Callable[[], bool] | None = None,
    ) -> None:
        self._delays = STALL_DELAYS if delays is None else delays
        self._sleep = sleep
        self._stop = stop or (lambda: False)

    def call(self, fn: Callable[[], T], notice: StallNotice | None = None) -> T:
        for attempt, delay in enumerate(self._delays, start=1):
            try:
                return fn()
            except MeridianError as exc:
                if not is_transient(exc) or self._stop():
                    raise
                logger.info("Connection stalled (attempt %d): %s; retrying in %.0fs", attempt, exc, delay)
                if notice is not None:
                    notice(attempt, delay, exc)
                self._sleep(delay)
        try:
            return fn()
        except MeridianError as exc:
            if is_transient(exc) and self._delays:
                logger.info("Connection stayed down after %d attempts; not retrying again", len(self._delays) + 1)
                self._delays = ()
            raise


def stall_event_notice(
    reporter: Reporter,
    operation: OperationContext,
    resource: ResourceRef | None = None,
) -> StallNotice:
    """Report each retry as a ``warning`` event that renderers show as a stalled connection."""

    def notice(attempt: int, delay: float, exc: MeridianError) -> None:
        emit_event(
            reporter,
            operation,
            WARNING,
            level="warning",
            phase="apply",
            resource=resource,
            message="connection stalled, retrying",
            data={"code": CONNECTION_STALLED, "attempt": attempt, "delay_seconds": delay, "error": str(exc)},
        )

    return notice
