"""Health, readiness polling, and connectivity primitives."""

from __future__ import annotations

import logging
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
) -> int:
    """Poll a readiness check until it passes or timeout expires.

    Args:
        check: Callable returning True when the service is ready.
        timeout: Maximum seconds to wait before raising.
        interval: Seconds between attempts.
        description: Human-readable label for error messages.

    Returns:
        The number of attempts taken.

    Raises:
        ReadinessTimeout: If the check never passes within *timeout*.
    """
    start = time.monotonic()
    attempts = 0
    while True:
        attempts += 1
        try:
            ready = check()
        except Exception:
            logger.debug("%s readiness check raised on attempt %d", description, attempts)
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
    """Test TCP connectivity to host:port using a Python socket."""
    import socket as _socket

    try:
        conn = _socket.create_connection((host, port), timeout=timeout)
        conn.close()
        return True
    except (OSError, _socket.timeout):
        return False
