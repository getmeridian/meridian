"""The person editor previews UTC dates and saves only edited schedule choices."""

import pytest

from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom

PENDING = {
    "activation_pending": True,
    "activation_duration_days": 30,
    "expire_at": None,
    "next_reset_at": "2027-02-28T00:00:00Z",
    "renewal_enabled": True,
    "next_renewal_at": None,
    "renewals_left": 2,
    "renewals_due": 0,
    "renewals_completed": 0,
}


def test_pending_person_names_the_duration_and_keeps_renewal_waiting() -> None:
    result = run_console(
        "person-schedules",
        data={
            "user": {
                "expire_at": None,
                "activation_duration_days": 30,
                "first_traffic_at": None,
                "renewal_interval_days": 7,
                "renewal_limit": 2,
                "schedule": PENDING,
            }
        },
    )
    assert "Valid for 30 days from first use" in result["shown"]
    assert "Waiting for first use" in result["shown"]
    assert "Renewals left" in result["shown"]
    assert "2027-02-28T00:00:00Z" in result["shown"]
    assert result["writes"] == []


def test_one_day_duration_uses_the_singular() -> None:
    result = run_console(
        "person-schedules",
        data={
            "user": {
                "expire_at": None,
                "activation_duration_days": 1,
                "schedule": {**PENDING, "activation_duration_days": 1},
            }
        },
    )
    assert "Valid for 1 day from first use" in result["shown"]


def test_schedule_editor_saves_all_changed_choices_in_one_patch() -> None:
    result = run_console(
        "person-schedules",
        data={
            "edits": {
                "expiry_mode": "first_use",
                "activation_duration_days": "30",
                "renewal_mode": "months",
                "renewal_interval": "1",
                "renewal_cap_mode": "limited",
                "renewal_limit": "4",
                "traffic_reset_anchor_day": "31",
                "traffic_limit_strategy": "month_rolling",
            },
            "schedulePreview": PENDING,
            "submit": True,
        },
    )
    assert result["writes"] == [
        {
            "url": "api/users/sample",
            "method": "PATCH",
            "body": {
                "expire_at": None,
                "activation_duration_days": 30,
                "renewal_interval_days": None,
                "renewal_interval_months": 1,
                "renewal_limit": 4,
                "traffic_reset_anchor_day": 31,
                "traffic_limit_strategy": "month_rolling",
            },
        }
    ]
    assert result["previews"]


def test_invalid_duration_never_sends_a_patch() -> None:
    result = run_console(
        "person-schedules",
        data={
            "edits": {
                "expiry_mode": "first_use",
                "activation_duration_days": "0",
            },
            "submit": True,
        },
    )
    assert result["error"] and result["writes"] == []


@pytest.mark.parametrize("zone", ["UTC", "America/New_York"])
def test_preview_renders_browser_zone_beside_the_exact_utc_instant(monkeypatch: pytest.MonkeyPatch, zone: str) -> None:
    monkeypatch.setenv("TZ", zone)
    result = run_console(
        "person-schedules",
        data={
            "edits": {"renewal_mode": "months", "renewal_interval": "1"},
            "schedulePreview": {
                **PENDING,
                "activation_pending": False,
                "expire_at": "2027-03-14T07:00:00Z",
                "next_renewal_at": "2027-03-14T07:00:00Z",
            },
        },
    )
    assert zone in result["shown"] and "2027-03-14T07:00:00Z" in result["shown"]
    assert result["writes"] == []
