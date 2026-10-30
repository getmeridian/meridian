"""Calendar renewals use UTC instants, short-month clamping, and finite client expiry rules."""

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from meridian.control.client import ControlClient
from meridian.control.models import UserUpdate
from meridian.core import client_limits
from tests.support.control_service import ADMIN_TOKEN, serve_control


@pytest.mark.parametrize(
    ("current", "months", "now", "expected"),
    [
        ("2027-01-31T12:34:56Z", 1, "2027-01-01T00:00:00Z", "2027-02-28T12:34:56Z"),
        ("2028-01-31T12:34:56Z", 1, "2027-01-01T00:00:00Z", "2028-02-29T12:34:56Z"),
        ("2027-01-31T12:34:56Z", 3, "2027-01-01T00:00:00Z", "2027-04-30T12:34:56Z"),
        ("2026-01-01T00:00:00Z", 1, "2027-01-31T12:34:56Z", "2027-02-28T12:34:56Z"),
        (None, 3, "2027-01-31T12:34:56Z", "2027-04-30T12:34:56Z"),
        ("2028-01-31T22:30:45.900+03:30", 1, "2027-01-01T00:00:00Z", "2028-02-29T19:00:45Z"),
    ],
)
def test_month_presets_use_the_later_instant_and_preserve_utc_time(current, months, now, expected) -> None:
    assert hasattr(client_limits, "preview_calendar_expiry")
    request = client_limits.CalendarRenewalRequest(months=months)
    preview = client_limits.preview_calendar_expiry(current, request, now=datetime.fromisoformat(now))
    assert preview.expire_at == expected
    assert preview.months == months


def test_calendar_renewal_refuses_the_legacy_no_expiry_range() -> None:
    assert hasattr(client_limits, "preview_calendar_expiry")
    with pytest.raises(client_limits.ClientLimitError, match="too far"):
        client_limits.preview_calendar_expiry(
            "2098-12-31T23:59:59Z", client_limits.CalendarRenewalRequest(months=1), now=datetime(2027, 1, 1, tzinfo=UTC)
        )


def test_month_preview_is_read_only_and_uses_the_service_account_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("meridian.control.person_expiry.utc_now", lambda: "2027-01-01T00:00:00Z")
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        admin.put_user("sample")
        admin.update_user("sample", UserUpdate(expire_at="2032-01-31T10:20:30Z"))
        before = admin.get_user("sample")
        reply = httpx.get(
            url + "/ui/api/people/sample/expiry?months=1", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}
        )
        assert reply.status_code == 200
        assert reply.json()["expire_at"] == "2032-02-29T10:20:30Z"
        assert admin.get_user("sample") == before


@pytest.mark.parametrize("query", ["months=0", "months=2", "months=1&months=3", "months=1&extra=1"])
def test_month_preview_rejects_invalid_choices_without_changing_the_user(query: str, tmp_path: Path) -> None:
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        before = admin.put_user("sample")
        reply = httpx.get(
            url + "/ui/api/people/sample/expiry?" + query, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}
        )
        assert reply.status_code == 400 and admin.get_user("sample") == before
