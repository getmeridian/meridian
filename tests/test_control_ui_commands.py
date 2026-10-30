"""Console cut-off consent and typed results, exercised through the actual shipped scripts."""

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


def run_ui(mode: str, locale: str = "en") -> dict[str, Any]:
    result = subprocess.run(
        ["node", str(ROOT / "tests/control_ui_commands.mjs"), str(UI_DIR), mode, locale],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return dict(json.loads(result.stdout))


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_console_removal_discloses_other_sessions_before_opt_in(locale: str) -> None:
    result = run_ui("fallback", locale)
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    assert result["writesBeforeConfirm"] == 0 and result["defaultOn"] is False
    assert catalog["commands.cut_off_help"] in result["disclosure"] and "main" in result["disclosure"]
    assert result["writes"] == [{"body": {"cut_off": True}, "csrf": "1"}]
    assert catalog["commands.core_restart"] in result["resultText"]


@pytest.mark.parametrize("mode", ["default", "cancel", "declared"])
def test_default_cancel_and_declared_people_do_not_request_a_cut_off(mode: str) -> None:
    result = run_ui(mode)
    if mode == "default":
        assert result["writes"] == [{"body": {"cut_off": False}, "csrf": "1"}]
    else:
        assert result["writes"] == []
    assert not any("/commands/" in request for request in result["requests"])


@pytest.mark.parametrize("mode", ["failed", "timed_out"])
def test_console_results_label_failures_and_timeouts_and_render_errors_as_text(mode: str) -> None:
    result = run_ui(mode)
    catalog = json.loads((UI_DIR / "locales/en.json").read_text())
    assert catalog["commands." + mode] in result["resultText"]
    assert result["images"] == 0 and result["pwned"] is False


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_console_restart_discloses_reconnects_and_sends_one_managed_unit_command(locale: str) -> None:
    result = run_ui("restart", locale)
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    assert result["writesBeforeConfirm"] == 0
    assert catalog["servers.health_degraded"] in result["serverText"]
    assert catalog["servers.applying"] in result["serverText"]
    assert catalog["servers.stats_stalled"] in result["serverText"]
    assert catalog["servers.memory"] in result["serverText"] and "26.7.28" in result["serverText"]
    assert catalog["commands.restart_text"] in result["disclosure"]
    assert result["writes"] == [{"body": {"kind": "restart_unit", "resource_id": "unit:netcore"}, "csrf": "1"}]
    assert catalog["commands.done"] in result["resultText"]


def test_console_restart_cancel_issues_no_command() -> None:
    assert run_ui("restart-cancel")["writes"] == []
