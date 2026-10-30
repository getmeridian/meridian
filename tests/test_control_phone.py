"""Dialogs and compact controls remain usable with a phone's viewport and touch input."""

from __future__ import annotations

import json

import pytest

from meridian.control.ui_routes import UI_DIR
from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


def test_nested_identity_checks_have_distinct_labelled_titles() -> None:
    result = run_console("phone-identity")
    assert len(result["titles"]) == len(set(result["titles"])) == 2
    assert result["labelsMatch"] is True


def test_long_dialogs_scroll_the_body_and_keep_form_actions_sticky() -> None:
    result = run_console("phone-dialog")
    assert result["bodyOverflow"] == "auto" and result["footerPosition"] == "sticky"
    assert "dvh" in result["maxHeight"]
    assert result["submitted"] is True and result["buttonsDisabled"] is True
    assert result["focusedScrolled"] is True


def test_compact_controls_meet_touch_target_size_with_a_coarse_pointer() -> None:
    result = run_console("phone-touch")
    assert all(size["height"] >= 44 and size["width"] >= 44 for size in result["targets"])


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_sign_out_has_a_name_when_its_visible_text_is_hidden(locale: str) -> None:
    result = run_console("phone-app", locale)
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    assert result["signOutName"] == catalog["auth.sign_out"]
