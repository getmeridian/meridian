"""Health, readiness polling, and connectivity primitives."""

from __future__ import annotations

import logging
import socket
import time
from typing import Callable

logger = logging.getLogger(__name__)


class ReadinessTimeout(Exception):
    """Raised when a readiness check does not pass within the timeout."""

    def __init__(self, description: str, timeout: float, attempts: int) -> None:
        super().__init__(f"{description} not ready after {timeout:.0f}s ({attempts} attempts)")
        self.description = description
        self.timeout = timeout
        self.attempts = attempts


def poll_until_ready(
    check: Callable[[], bool],
    *,
    timeout: float = 60,
    interval: float = 2.0,
    description: str = "service",
    transient: tuple[type[Exception], ...] = (),
) -> int:
    """Poll ``check`` every ``interval`` seconds; return the attempt count or raise ``ReadinessTimeout``.

    Exceptions listed in ``transient`` count as not ready yet; any other exception propagates.
    """
    start = time.monotonic()
    attempts = 0
    while True:
        attempts += 1
        try:
            ready = check()
        except transient as exc:
            logger.debug("%s not ready on attempt %d: %s", description, attempts, exc)
            ready = False
        if ready:
            logger.debug("%s ready after %d attempt(s)", description, attempts)
            return attempts
        elapsed = time.monotonic() - start
        if elapsed >= timeout:
            raise ReadinessTimeout(description, timeout, attempts)
        remaining = timeout - elapsed
        time.sleep(min(interval, remaining))


def tcp_connect(host: str, port: int, timeout: float = 5) -> bool:
    """Whether a TCP connection to host:port opens within ``timeout`` seconds."""
    try:
        socket.create_connection((host, port), timeout=timeout).close()
    except OSError:
        return False
    return True
