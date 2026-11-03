"""Explicit unlimited and fleet inheritance remain distinct in the device editor."""

import json

import pytest

from meridian.control.ui_routes import UI_DIR
from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


def scenario(cap: int | None, mode: str = "view", **changes: object) -> dict:
    return run_console(
        "person-devices",
        data={
            "user": {"username": "example", "device_limit": cap},
            "devices": {"level": "private", "count": 4, "limit": 0 if cap == 0 else 3, "devices": []},
            "action": "view" if mode == "view" else "cap",
            "capMode": mode,
            "value": None,
            **changes,
        },
    )


def test_unlimited_cap_is_named_in_the_editor_and_device_count() -> None:
    result = scenario(0)
    assert "Unlimited" in result["shown"]
    assert "4 devices · unlimited" in result["shown"]
    assert "4 of 0" not in result["shown"]


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
@pytest.mark.parametrize("cap", [0, 5], ids=["unlimited", "limited"])
def test_device_slots_show_native_counts_without_changing_the_limit(locale: str, cap: int) -> None:
    result = run_console(
        "person-devices",
        locale,
        {
            "user": {"username": "example", "device_limit": cap},
            "devices": {"level": "private", "count": 4, "limit": cap, "devices": []},
            "action": "view",
        },
    )
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    expected = catalog["devices.unlimited_count" if cap == 0 else "devices.count"]
    expected = expected.replace("{count}", "۴" if locale == "fa" else "4").replace(
        "{limit}", "۵" if locale == "fa" else "5"
    )
    assert expected in result["shown"]
    assert all(request["method"] == "GET" for request in result["requests"])


@pytest.mark.parametrize(("old", "mode", "cap"), [(3, "unlimited", 0), (0, "default", None)])
def test_explicit_choices_send_zero_for_exempt_and_null_for_inherit(old: int, mode: str, cap: int | None) -> None:
    result = scenario(old, mode)
    patches = [item for item in result["requests"] if item["method"] == "PATCH"]
    assert patches == [{"method": "PATCH", "path": "api/users/example", "body": {"device_limit": cap}}]


@pytest.mark.parametrize("cap", [None, 0, 5], ids=["inherit", "unlimited", "custom"])
def test_an_untouched_device_editor_never_reads_as_unsaved(cap: int | None) -> None:
    assert scenario(cap)["dirtyAtLoad"] is False
