"""Person actions use their returned account and cannot replace another screen."""

import json

import pytest

from meridian.control.ui_routes import UI_DIR
from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


@pytest.mark.parametrize("action", ["toggle", "save", "metadata", "reset", "clear", "reissue", "remove"])
@pytest.mark.parametrize("navigate", ["servers", "person", "return"])
def test_person_mutation_cannot_replace_a_screen_opened_after_it_started(action: str, navigate: str) -> None:
    result = run_console("person-mutation", data={"action": action, "navigate": navigate})
    assert len(result["started"]) == 1
    assert result["after"] == result["before"]
    assert "Sample person" in result["toasts"][-1]


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
@pytest.mark.parametrize("status", ["active", "expired", "limited"])
def test_enable_feedback_names_the_person_and_explains_returned_access(locale: str, status: str) -> None:
    result = run_console("person-mutation", locale, {"status": status})
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    key = "person.enabled_toast" if status == "active" else f"person.enabled_{status}_toast"
    assert result["toasts"] == [catalog[key].replace("{name}", "Sample person")]
    assert result["status"] == catalog[f"status.{status}"]
    assert result["readsAfterResult"] == 0
