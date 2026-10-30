"""Private note and tag edits through the person view."""

from __future__ import annotations

import json

import pytest

from meridian.control.ui_routes import UI_DIR
from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
@pytest.mark.parametrize("change", [{"note": ""}, {"tag": ""}, {"note": "Updated\nnote", "tag": "TRIAL_7"}])
def test_person_metadata_is_plain_text_localized_and_patches_only_edits(locale: str, change: dict[str, str]) -> None:
    user = {
        "username": "sample",
        "status": "active",
        "created_at": "2026-10-29T00:00:00Z",
        "note": '<img src=x onerror="window.pwned=1">\nPrivate text',
        "tag": "GROUP_1",
        "expire_at": None,
        "traffic_limit_bytes": 1234,
        "traffic_limit_strategy": "month",
    }
    result = run_console("metadata", locale, {"user": user, "change": change})
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    assert result["initialNote"] == user["note"] and result["initialTag"] == user["tag"]
    assert catalog["person.note"] in result["text"] and catalog["person.tag"] in result["text"]
    assert result["injected"] is False
    assert result["writes"] == [change]


@pytest.mark.parametrize("change", [{"tag": "A-B"}, {"note": "x" * 501}])
def test_invalid_metadata_is_explained_before_submission(change: dict[str, str]) -> None:
    result = run_console(
        "metadata",
        "en",
        {
            "user": {
                "username": "sample",
                "status": "active",
                "created_at": "2026-10-29T00:00:00Z",
                "expire_at": None,
                "traffic_limit_bytes": 0,
                "traffic_limit_strategy": "no_reset",
            },
            "change": change,
        },
    )
    assert result["writes"] == []
    assert result["error"]


def test_a_lowercase_tag_is_saved_in_uppercase() -> None:
    user = {
        "username": "sample",
        "status": "active",
        "created_at": "2026-10-29T00:00:00Z",
        "expire_at": None,
        "traffic_limit_bytes": 0,
        "traffic_limit_strategy": "no_reset",
    }
    result = run_console("metadata", "en", {"user": user, "change": {"tag": "family"}})
    assert result["writes"] == [{"tag": "FAMILY"}]
