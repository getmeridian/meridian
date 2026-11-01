"""The localized page reveals counts only and never saves device information."""

import json

import pytest

from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


def account(slots: object = None) -> dict:
    user = {
        "userStatus": "ACTIVE",
        "trafficUsedBytes": "0",
        "trafficLimitBytes": "0",
        "lifetimeTrafficUsedBytes": "0",
        "expiresAt": "2099-12-31T23:59:59Z",
        "daysLeft": 100,
    }
    if slots is not None:
        user["deviceSlots"] = slots
    return {"isFound": True, "user": user}


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_account_shows_slots_and_explains_full_and_uncounted_apps_without_saving_counts(locale: str) -> None:
    result = run_console(
        "account-slots", locale, {"info": account({"used": 2, "allowed": 3, "device_key": "private-device"})}
    )
    digits = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789", "\u2068\u2069")
    assert any("2 / 3" in metric.translate(digits) for metric in result["metrics"])
    assert "private-device" not in result["text"]
    hint = {"en": "not counted", "ru": "не учитываются", "fa": "شمرده نمی‌شوند", "zh": "不计数"}[locale]
    assert hint in result["text"]
    assert "deviceSlots" not in result["saved"] and "private-device" not in json.dumps(result["saved"])
    assert result["requests"][0]["cache"] == "no-store"


@pytest.mark.parametrize(
    "slots", [None, {"used": -1, "allowed": 3}, {"used": 1, "allowed": 0}, {"used": "2", "allowed": 3}]
)
def test_missing_or_invalid_optional_slot_counts_never_appear_or_block_account(slots: object) -> None:
    result = run_console("account-slots", "en", {"info": account(slots)})
    assert "Active" in result["text"] and "Device slots" not in result["text"]


def test_fresh_info_without_slots_removes_the_previous_count() -> None:
    result = run_console("account-slots-refresh", "en", {"info": account({"used": 3, "allowed": 2}), "next": account()})
    assert "3 / 2" in result["text"].translate(str.maketrans("", "", "\u2068\u2069"))
    assert "Device slots" not in result["after"]
