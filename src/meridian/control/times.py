"""RFC 3339 UTC timestamps as the agent protocol writes them (whole seconds, `Z`)."""

from __future__ import annotations

from datetime import UTC, datetime

_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def utc_now() -> str:
    return datetime.now(UTC).strftime(_FORMAT)


def format_timestamp(seconds: float) -> str:
    return datetime.fromtimestamp(int(seconds), UTC).strftime(_FORMAT)


def parse_timestamp(text: str) -> float:
    """Whole seconds since the epoch of a validated protocol timestamp (a fraction is dropped)."""
    return datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC).timestamp()
