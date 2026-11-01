"""Account pages describe first use and dates without activating or exposing metadata."""

import json

import pytest

from tests.support.console_dom import needs_console_dom, run_console
from tests.test_console_account_schedules import PENDING

pytestmark = needs_console_dom


def info(schedule: object, expiry: str = "2099-12-31T23:59:59.000Z") -> dict:
    return {
        "isFound": True,
        "user": {
            "userStatus": "ACTIVE",
            "trafficUsedBytes": "0",
            "trafficLimitBytes": "0",
            "lifetimeTrafficUsedBytes": "0",
            "expiresAt": expiry,
            "daysLeft": 30,
            "accountSchedule": schedule,
            "username": "example",
        },
    }


@pytest.mark.parametrize(
    ("locale", "text"),
    [
        ("en", "Valid for 30 days from first use"),
        ("ru", "Доступ на 30 дней с первого использования"),
        ("fa", "از اولین استفاده به‌مدت ۳۰ روز معتبر است"),
        ("zh", "首次使用后有效 30 天"),
    ],
)
def test_pending_duration_is_native_and_only_same_origin_info_is_fetched(locale: str, text: str) -> None:
    result = run_console("page-schedules", locale, {"info": info(PENDING)})
    assert text in result["shown"]
    assert result["requests"] == [
        {"url": "https://198.51.100.1/sub/exampleToken0001/info", "method": "GET", "cache": "no-store"}
    ]
    assert result["importDisabled"] is None
    assert "example" not in json.dumps(result["stored"])
    assert "198.51.100.1" not in json.dumps(result["stored"])


def test_activated_account_shows_real_expiry_next_reset_and_renewal_with_utc() -> None:
    schedule = {
        **PENDING,
        "activation_pending": False,
        "expire_at": "2027-03-14T07:00:00Z",
        "next_renewal_at": "2027-03-14T07:00:00Z",
    }
    result = run_console("page-schedules", data={"info": info(schedule, schedule["expire_at"])})
    assert "Valid for 30 days" not in result["shown"]
    assert "2027-03-14T07:00:00Z" in result["shown"] and "2027-02-28T00:00:00Z" in result["shown"]
    assert "Next renewal" in result["shown"] and "Renewals left" in result["shown"]


def test_bad_optional_schedule_never_blocks_a_valid_account() -> None:
    result = run_console("page-schedules", data={"info": info({**PENDING, "activation_duration_days": 0})})
    assert "Active" in result["shown"] and "No expiry" in result["shown"]
    assert result["importDisabled"] is None
