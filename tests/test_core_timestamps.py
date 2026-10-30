"""Shared wire timestamps reject impossible dates without changing their text."""

import pytest
from pydantic import TypeAdapter, ValidationError

from meridian.core.agent_types import Timestamp


@pytest.mark.parametrize(
    "timestamp",
    [
        "0000-01-01T00:00:00Z",
        "2026-13-01T00:00:00Z",
        "2026-01-00T00:00:00Z",
        "2026-02-29T00:00:00Z",
        "2026-02-31T00:00:00Z",
        "2026-01-01T24:00:00Z",
        "2026-01-01T00:60:00Z",
        "2026-01-01T00:00:60Z",
    ],
)
def test_timestamp_rejects_impossible_dates_and_times(timestamp: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(Timestamp).validate_python(timestamp)


@pytest.mark.parametrize("timestamp", ["2028-02-29T23:59:59Z", "2026-01-01T00:00:00.123456789Z"])
def test_timestamp_preserves_valid_calendar_dates_and_fractional_text(timestamp: str) -> None:
    assert TypeAdapter(Timestamp).validate_python(timestamp) == timestamp
