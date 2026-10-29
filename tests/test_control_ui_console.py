"""Run the console's interactions against local DOM and mocked same-origin replies."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from meridian.control.ui_routes import UI_DIR

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not (ROOT / "website/node_modules/jsdom").exists(),
    reason="needs node and the website's installed jsdom dependency",
)


def run_console(mode: str, locale: str = "en", data: Any = None) -> dict[str, Any]:
    result = subprocess.run(
        ["node", str(ROOT / "tests/control_ui_console.mjs"), str(UI_DIR), mode, locale, json.dumps(data)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return dict(json.loads(result.stdout))


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
