"""Token duration choices, owner step-up, and expired-state display in the shipped DOM."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from meridian.control.ui_routes import UI_DIR
from tests.support.console_dom import needs_console_dom

ROOT = Path(__file__).resolve().parents[1]
pytestmark = needs_console_dom


def _run(mode: str = "default", locale: str = "en") -> dict[str, Any]:
    result = subprocess.run(
        ["node", str(ROOT / "tests/control_ui_tokens.mjs"), str(UI_DIR), mode, locale],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return dict(json.loads(result.stdout))


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_new_console_tokens_default_to_ninety_days_and_keep_the_body_after_step_up(locale: str) -> None:
    result = _run(locale=locale)
    assert result["defaultExpiry"] == "90"
    assert result["expiries"] == ["7", "30", "90", "never"]
    assert "people" in result["scopes"]
    assert len(result["writes"]) == 2
    assert all(write["csrf"] == "1" for write in result["writes"])
    assert (
        result["writes"][0]["body"]
        == result["writes"][1]["body"]
        == {
            "name": "People desk",
            "scope": "people",
            "expires_in_days": 90,
        }
    )
    assert result["events"] == ["GET:api/tokens", "POST:api/tokens", "step-up", "POST:api/tokens", "GET:api/tokens"]


@pytest.mark.parametrize(("choice", "days"), [("7", 7), ("30", 30), ("90", 90), ("never", None)])
def test_the_console_sends_the_chosen_duration_or_explicit_null(choice: str, days: int | None) -> None:
    result = _run(choice)
    assert result["writes"][-1]["body"]["expires_in_days"] == days


def test_cancelling_token_creation_sends_no_write_or_step_up() -> None:
    result = _run("cancel")
    assert result["writes"] == [] and "step-up" not in result["events"]


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_expired_tokens_are_dimmed_and_expiry_or_never_is_shown_in_every_locale(locale: str) -> None:
    result = _run("cancel", locale)
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    expired, future, permanent = result["rows"]
    assert expired["dim"] and not future["dim"] and not permanent["dim"]
    assert catalog["tokens.expired"].split("{when}")[0] in expired["text"]
    assert catalog["tokens.expires"].split("{when}")[0] in future["text"]
    assert catalog["tokens.expires_never"] in permanent["text"]
