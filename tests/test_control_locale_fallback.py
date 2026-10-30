"""A failed locale never discards a successfully loaded English fallback."""

from __future__ import annotations

import json

import pytest

from meridian.control.ui_routes import UI_DIR
from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


@pytest.mark.parametrize("locale", ["ru", "fa", "zh"])
def test_a_failed_selected_locale_keeps_english_even_when_english_arrives_later(locale: str) -> None:
    result = run_console("locale-fallback", locale)
    english = json.loads((UI_DIR / "locales/en.json").read_text())
    assert result["label"] == english["nav.people"] and result["lang"] == "en"
    assert result["dir"] == "ltr"


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_unavailable_catalogs_offer_a_local_retry_and_recover(locale: str) -> None:
    result = run_console("locale-retry", locale)
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    assert catalog["i18n.unavailable"] in result["notice"]
    assert result["recovered"] is True and result["noticeAfter"] == ""
    assert result["label"] == catalog["nav.people"]


@pytest.mark.parametrize(("previous", "selected"), [("ru", "fa"), ("fa", "zh"), ("zh", "ru"), ("fa", "en")])
@pytest.mark.parametrize("english_ready", [False, True], ids=["no-english-cache", "english-cache"])
def test_an_offline_language_switch_keeps_the_working_catalog_language_and_direction(
    previous: str, selected: str, english_ready: bool
) -> None:
    result = run_console("locale-switch", selected, {"previous": previous, "englishReady": english_ready})
    previous_catalog = json.loads((UI_DIR / f"locales/{previous}.json").read_text())
    assert result["before"]["label"] == previous_catalog["nav.people"]
    assert result["after"] == result["before"]
    assert result["switchResolved"] is True
    selected_catalog = json.loads((UI_DIR / f"locales/{selected}.json").read_text())
    assert selected_catalog["i18n.unavailable"] in result["notice"]
    assert result["recovered"] is True and result["noticeAfter"] == ""
    assert result["label"] == selected_catalog["nav.people"] and result["lang"] == selected
    assert result["dir"] == ("rtl" if selected == "fa" else "ltr")
