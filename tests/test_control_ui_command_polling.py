"""Per-node truth survives sibling timeouts and command-result polling errors."""

from __future__ import annotations

import json

import pytest

from meridian.control.ui_routes import UI_DIR
from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


@pytest.mark.parametrize("effect", [None, "core_restart"])
def test_review_4_1_only_nodes_with_pending_records_time_out(effect: str | None) -> None:
    result = run_console("commands-timeout", data={"initialA": "succeeded", "effect": effect, "advance": 300000})
    catalog = json.loads((UI_DIR / "locales/en.json").read_text())
    label = catalog["commands.core_restart" if effect else "commands.done"]
    assert result["first"]["node-a"] == "node-a: " + label
    assert result["last"]["node-a"] == "node-a: " + label
    assert result["last"]["node-b"] == "node-b: " + catalog["commands.timed_out"]


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_review_4_2_terminal_poll_errors_stop_with_error_without_relabelling_finished_nodes(status: int) -> None:
    result = run_console(
        "commands-error", data={"initialA": "succeeded", "effect": "core_restart", "errorStatus": status}
    )
    catalog = json.loads((UI_DIR / "locales/en.json").read_text())
    assert result["last"]["node-a"] == "node-a: " + catalog["commands.core_restart"]
    assert catalog["commands.timed_out"] not in result["text"]
    assert result["last"]["node-b"] == "node-b: " + catalog["commands.read_error"]
    assert result["beforeStop"] == result["afterStop"] == 1
    assert "cannot read the command" in result["text"]
    assert result["images"] == 0 and result["pwned"] is False


def test_review_4_3_one_failed_get_keeps_sibling_results_from_the_same_poll() -> None:
    result = run_console("commands-partial", data={"errorStatus": 503})
    catalog = json.loads((UI_DIR / "locales/en.json").read_text())
    assert result["first"]["node-a"] == "node-a: " + catalog["commands.done"]
    assert result["last"]["node-a"] == "node-a: " + catalog["commands.done"]
    assert result["last"]["node-b"] == "node-b: " + catalog["commands.pending"]
    assert sum("/node-a/" in path for path in result["firstReads"]) == 1
    assert "cannot read the command" in result["text"]
