"""Bulk expiry validates synchronously before a value or submission can clear it."""

import pytest

from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
@pytest.mark.parametrize(
    ("date", "time", "never", "valid", "expected"),
    [
        ("", "23:59:59", False, False, None),
        ("not-a-date", "23:59:59", False, False, None),
        ("2026-11-02", "", False, False, None),
        ("2026-11-02", "bad-time", False, False, None),
        ("2026-10-31", "23:59:59", False, False, None),
        ("2099-01-01", "00:00:00", False, False, None),
        ("2026-11-02", "12:00:00", False, True, "2026-11-02T12:00:00Z"),
        ("", "", True, True, None),
    ],
    ids=["blank", "invalid-date", "blank-time", "invalid-time", "past", "2099", "future", "no-expiry"],
)
def test_bulk_expiry_value_and_immediate_submit_require_a_valid_choice(locale, date, time, never, valid, expected):
    result = run_console("people-bulk-expiry", locale, {"date": date, "time": time, "never": never})
    value = {"expire_at": expected} if valid else None
    assert result["direct"] == result["submitted"] == value
    if valid:
        assert result["directError"] == result["formError"] == ""
    else:
        assert result["directError"] and result["formError"] and result["invalidFocused"]
    assert result["stored"] == [] and result["requests"] == []
