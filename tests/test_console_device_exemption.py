"""Explicit unlimited and fleet inheritance remain distinct in the device editor."""

import pytest

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


@pytest.mark.parametrize(("old", "mode", "cap"), [(3, "unlimited", 0), (0, "default", None)])
def test_explicit_choices_send_zero_for_exempt_and_null_for_inherit(old: int, mode: str, cap: int | None) -> None:
    result = scenario(old, mode)
    patches = [item for item in result["requests"] if item["method"] == "PATCH"]
    assert patches == [{"method": "PATCH", "path": "api/users/example", "body": {"device_limit": cap}}]
