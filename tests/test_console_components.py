"""Shared console components keep their accessibility contract in every locale."""

import pytest

from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_a_tip_opens_one_explanation_at_a_time_and_closes_on_escape_or_outside_click(locale: str) -> None:
    result = run_console("component-tip", locale)
    assert result["initial"]["expanded"] == "false" and result["initial"]["hidden"]
    assert result["initial"]["text"] == "" and result["initial"]["role"] == "status"
    assert "Traffic" in result["initial"]["label"] and result["initial"]["label"] != "Traffic"
    assert result["opened"]["expanded"] == "true" and not result["opened"]["hidden"]
    assert result["opened"]["text"] == "How much data this person may use."
    assert result["switched"]["first"]["hidden"] and result["switched"]["first"]["expanded"] == "false"
    assert result["switched"]["second"]["text"] == "Apps that fetched the link."
    assert result["toggledClosed"]["hidden"] and result["toggledClosed"]["text"] == ""
    assert result["escaped"]["hidden"] and result["escaped"]["focused"]
    assert result["outside"]["hidden"]


def test_a_tip_inside_a_label_does_not_toggle_its_control() -> None:
    result = run_console("component-tip")
    assert not result["label"]["tip"]["hidden"] and result["label"]["checked"] is False
    assert result["tabbedAway"]["hidden"] and result["tabbedAway"]["expanded"] == "false"
    assert result["disclosureClosed"]["hidden"] and result["disclosureClosed"]["text"] == ""


def test_a_toast_action_runs_once_and_closes_while_plain_toasts_offer_none() -> None:
    result = run_console("component-toast")
    assert result["offered"] == {
        "shown": True,
        "action": True,
        "label": "Undo",
        "type": "button",
        "text": "Disabled Kavi.Undo",
    }
    assert result["undone"] == {"runs": 1, "shown": False, "action": False, "waits": True}
    assert result["plain"] == {"shown": True, "action": False, "buttons": 0, "waits": True}
