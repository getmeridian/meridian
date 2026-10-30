"""Execute the device card: private rendering, confirmed removals, and partial cap edits."""

from __future__ import annotations

from typing import Any

import pytest

from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


def card(
    *,
    level: str = "private",
    limit: int | None = None,
    action: str = "view",
    value: str | None = None,
    cancel: bool = False,
    bad_input: bool = False,
) -> dict[str, Any]:
    options = {
        "user": {"username": "alice", "device_limit": limit},
        "action": action,
        "value": value,
        "cancel": cancel,
        "badInput": bad_input,
        "devices": {
            "level": level,
            "count": 1,
            "limit": 3,
            "devices": [
                {
                    "device_key": "a" * 64,
                    "platform": "Example OS",
                    "os_version": "1.0",
                    "model": "<img src=x onerror=alert(1)>",
                    "user_agent": "Example app",
                    "first_seen_at": "2026-10-29T12:00:00Z",
                    "last_seen_at": "2026-10-29T13:00:00Z",
                }
            ],
        },
    }
    return run_console("person-devices", data=options)


def test_private_card_never_renders_ids_metadata_or_fetch_times() -> None:
    result = card()
    assert "1 of 3 devices" in result["shown"]
    assert all(value not in result["shown"] for value in ("a" * 64, "Example OS", "Example app", "2026-10-29"))


def test_full_card_shows_system_version_and_keeps_hostile_metadata_as_text() -> None:
    result = card(level="full")
    assert "a" * 64 in result["shown"] and "Example OS 1.0" in result["shown"]
    assert "<img src=x onerror=alert(1)>" in result["shown"] and "Example app" in result["shown"]
    assert "img" not in result["tags"] and "script" not in result["tags"]


@pytest.mark.parametrize(
    ("old", "value", "body"),
    [(None, None, None), (2, "2", None), (2, "", {"device_limit": None}), (None, "3", {"device_limit": 3})],
    ids=["untouched-default", "untouched-cap", "clear-override", "set-override"],
)
def test_cap_save_names_only_the_edited_cap_and_default_is_null(
    old: int | None, value: str | None, body: dict | None
) -> None:
    result = card(limit=old, action="cap", value=value)
    patches = [request for request in result["requests"] if request["method"] == "PATCH"]
    assert patches == ([] if body is None else [{"method": "PATCH", "path": "api/users/alice", "body": body}])


@pytest.mark.parametrize("value", ["0", "101", "1.5"], ids=["zero", "over-100", "fraction"])
def test_an_invalid_cap_cannot_clear_or_change_the_saved_override(value: str) -> None:
    result = card(limit=2, action="cap", value=value)
    assert result["error"] and all(request["method"] == "GET" for request in result["requests"])
    assert card(limit=2, action="cap", value="", bad_input=True)["error"]


@pytest.mark.parametrize(
    ("action", "level", "suffix"),
    [("clear", "private", ""), ("remove", "full", "/" + "a" * 64)],
    ids=["private-clear", "full-remove"],
)
def test_a_confirmed_removal_forgets_only_the_chosen_device_slots(action: str, level: str, suffix: str) -> None:
    result = card(level=level, action=action)
    deletes = [request for request in result["requests"] if request["method"] == "DELETE"]
    assert deletes == [{"method": "DELETE", "path": "api/users/alice/devices" + suffix}]
    assert result["refreshed"] == 0
    assert len([request for request in result["requests"] if request["method"] == "GET"]) == 2
    assert all(request["method"] == "GET" for request in card(level=level, action=action, cancel=True)["requests"])
