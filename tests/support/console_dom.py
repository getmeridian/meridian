"""Console DOM scenarios: Node plus the website's installed jsdom."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from meridian.control.ui_routes import UI_DIR

ROOT = Path(__file__).resolve().parents[2]

# CI sets MERIDIAN_TEST_CONSOLE where it installs jsdom, so a lost install fails instead of skipping.
needs_console_dom = pytest.mark.skipif(
    not os.environ.get("MERIDIAN_TEST_CONSOLE")
    and (shutil.which("node") is None or not (ROOT / "website/node_modules/jsdom").exists()),
    reason="needs node and the website's installed jsdom dependency",
)


def run_console(mode: str, locale: str = "en", data: Any = None) -> dict[str, Any]:
    runners = {
        "page-": "control_ui_page.mjs",
        "people-triage": "control_ui_people.mjs",
        "account-": "connection_account.mjs",
        "command-": "control_ui_commands.mjs",
        "showcase-": "control_ui_showcase.mjs",
        "person-": "control_ui_person.mjs",
    }
    runner = next((name for prefix, name in runners.items() if mode.startswith(prefix)), "control_ui_console.mjs")
    mode = mode.removeprefix("command-")
    result = subprocess.run(
        ["node", str(ROOT / "tests" / runner), str(UI_DIR), mode, locale, json.dumps(data)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return dict(json.loads(result.stdout))
