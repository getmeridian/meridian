"""Run the console's interactions against local DOM and mocked same-origin replies."""

from __future__ import annotations

import json

import pytest

from meridian.control.ui_routes import UI_DIR
from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_server_cards_separate_runtime_reachability_and_convergence(locale: str) -> None:
    result = run_console("servers", locale)
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    assert len(result["badges"]) == 3
    for key in ("servers.memory", "servers.disk", "servers.load", "servers.uptime", "servers.core_uptime"):
        assert catalog[key] in result["text"]
    assert catalog["servers.stats_stalled"] in result["text"]
    assert "stats" in result["text"] and "last collected 120 s ago" in result["text"]
    assert ("۴۲" if locale == "fa" else "42") in result["text"] and "Xray 26.7.28" in result["text"]
    assert "core-unit: unit exited" in result["text"]
