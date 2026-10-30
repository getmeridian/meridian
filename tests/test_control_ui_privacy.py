"""Actual DOM behavior of privacy consent and UTC charts, with offline website test dependencies."""

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


def _run(mode: str, locale: str = "en") -> dict[str, Any]:
    result = subprocess.run(
        ["node", str(ROOT / "tests/control_ui_privacy.mjs"), str(UI_DIR), mode, locale],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return dict(json.loads(result.stdout))


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_privacy_discloses_before_writing_and_retries_only_after_owner_step_up(locale: str) -> None:
    result = _run("privacy", locale)
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    assert result["writesBeforeConfirm"] == 0
    assert catalog["privacy.devices_private"] in result["dialog"]
    assert catalog["privacy.restore_private"] in result["dialog"]
    assert len(result["writes"]) == 2 and all(write["csrf"] == "1" for write in result["writes"])
    assert (
        result["writes"][0]["body"]
        == result["writes"][1]["body"]
        == {
            "device_limits": "private",
            "default_device_limit": 3,
            "usage_history": "daily",
            "history_days": 7,
        }
    )
    requests = [event for event in result["events"] if event.startswith(("GET:", "PUT:")) or event == "step-up"]
    assert requests == ["GET:api/privacy", "PUT:api/privacy", "step-up", "PUT:api/privacy"]


@pytest.mark.parametrize("mode", ["cancel", "unchanged"])
def test_cancelling_or_saving_unchanged_privacy_sends_no_write_and_no_step_up(mode: str) -> None:
    result = _run(mode)
    assert result["writes"] == [] and "step-up" not in result["events"]
    assert result["error"] == ""


def test_a_bad_number_is_refused_before_consent_or_any_api_write() -> None:
    result = _run("bad-number")
    assert result["writes"] == [] and result["dialog"] == ""
    assert "whole-number" in result["error"]


def test_turning_tracking_off_keeps_disabled_defaults_even_after_invalid_edits() -> None:
    result = _run("lower-invalid")
    assert "All device records will be deleted" in result["dialog"]
    assert "All daily history" in result["dialog"]
    assert result["settings"] == {
        "device_limits": "off",
        "default_device_limit": 3,
        "usage_history": "off",
        "history_days": 90,
    }


def test_an_audit_author_that_looks_like_html_is_only_text() -> None:
    result = _run("malicious")
    assert result["images"] == 0 and not result["pwned"]
    assert '<img src=x onerror="window.pwned=1">' in result["text"]


def test_chart_elements_use_svg_namespaces_and_keep_zero_days_and_finite_bar_heights() -> None:
    result = _run("chart")
    assert set(result["svgNamespaces"]) == {"http://www.w3.org/2000/svg"}
    assert len(result["bars"]) == 7 and all(1 <= bar["height"] <= 160 for bar in result["bars"])
    assert "0 B" in result["bars"][0]["label"]
    assert "UTC" in result["bars"][-1]["label"] and "Show daily totals" in result["text"]


def test_a_late_usage_failure_cannot_replace_the_newer_chart_or_its_retention_label() -> None:
    result = _run("race")
    assert result["afterOlder"] == result["afterNewest"]
    assert result["range"] == "14" and len(result["bars"]) == 14
    assert "14" in result["text"] and "UTC days" in result["text"]


def test_the_fleet_chart_can_pick_a_node_without_another_request() -> None:
    result = _run("fleet")
    assert "7 B" in result["bars"][0]["label"]
    assert [event for event in result["events"] if event.startswith("GET:")] == ["GET:api/traffic/daily?days=30"]


def test_history_off_is_a_plain_notice_with_a_privacy_link_and_no_chart() -> None:
    result = _run("history-off")
    assert result["bars"] == [] and result["links"] == ["#/settings"]
    assert "Daily history is off" in result["text"]


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_cli_and_console_disclosures_have_the_same_privacy_cost_in_every_locale(locale: str) -> None:
    cli = json.loads((ROOT / f"src/meridian/i18n/locales/{locale}.json").read_text())
    ui = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    for key in ("privacy.devices_private", "privacy.devices_full", "privacy.history_daily", "privacy.restore_private"):
        assert ui[key] == cli[key]
