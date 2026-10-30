"""A failed locale never discards a successfully loaded English fallback."""

from __future__ import annotations

import json

import pytest

from meridian.control.ui_routes import UI_DIR
from tests.test_control_ui_console import run_console


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
