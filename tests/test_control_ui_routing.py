"""Settings explains the topology-owned policy in every locale and offers only a CLI command."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from meridian.control.ui_routes import UI_DIR
from tests.support.console_dom import needs_console_dom

ROOT = Path(__file__).resolve().parents[1]
pytestmark = needs_console_dom


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
@pytest.mark.parametrize("policy", ["absent", "ru-split", "full"])
def test_settings_shows_the_policy_with_an_explanation_and_a_command_but_no_controls(locale: str, policy: str) -> None:
    response = subprocess.run(
        ["node", str(ROOT / "tests/control_ui_routing.mjs"), str(UI_DIR), locale, policy],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    result = json.loads(response.stdout)
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    shown = "ru-split" if policy == "absent" else policy
    explanation = "settings.routing_full" if shown == "full" else "settings.routing_ru_split"
    assert shown in result["text"] and catalog[explanation] in result["text"]
    command = "meridian fleet routing ru-split" if shown == "full" else "meridian fleet routing full"
    assert command in result["text"]
    assert result["controls"] == 0
    assert all(request["method"] == "GET" for request in result["requests"])
