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
        "shell-": "control_ui_shell.mjs",
        "overview-": "control_ui_shell.mjs",
        "delivery-person": "control_ui_delivery_policy.mjs",
        "policy-preview": "control_ui_delivery_policy.mjs",
        "token-expiry-": "control_ui_tokens.mjs",
        "passkey-": "control_ui_passkeys.mjs",
        "profiles-": "control_ui_profiles.mjs",
        "page-": "control_ui_page.mjs",
        "people-bulk-expiry": "control_ui_bulk_expiry.mjs",
        "people-bulk": "control_ui_people_bulk.mjs",
        "people-triage-ux": "control_ui_people_ux.mjs",
        "people-triage": "control_ui_people.mjs",
        "account-": "connection_account.mjs",
        "command-": "control_ui_commands.mjs",
        "showcase-": "control_ui_showcase.mjs",
        "person-tabs-": "control_ui_person_tabs.mjs",
        "settings-": "control_ui_settings.mjs",
        "records-": "control_ui_records.mjs",
        "person-": "control_ui_person.mjs",
        "component-": "control_ui_components.mjs",
    }
    runner = next((name for prefix, name in runners.items() if mode.startswith(prefix)), "control_ui_console.mjs")
    if mode.startswith("people-triage"):
        from tests.support.people_queries import with_people_replies

        data = with_people_replies(data)
    mode = mode.removeprefix("command-")
    if mode.startswith(("token-", "records-")):
        from meridian.control.token_capabilities import capability_catalog

        data = {**(data or {}), "capability_catalog": capability_catalog().to_data()}
    mode = mode.removeprefix("token-expiry-")
    # The scenario travels on stdin: Linux caps a single argument at 128 KiB.
    result = subprocess.run(
        ["node", str(ROOT / "tests" / runner), str(UI_DIR), mode, locale, "-"],
        cwd=ROOT,
        input=json.dumps(data),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return dict(json.loads(result.stdout))
