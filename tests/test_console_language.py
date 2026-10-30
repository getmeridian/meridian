"""Default-language and person-override controls use the existing console forms."""

from __future__ import annotations

import json

import pytest

from meridian.control.ui_routes import UI_DIR
from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_settings_offers_auto_and_explains_the_person_can_switch(locale: str) -> None:
    result = run_console("delivery-language", locale, {"language": "auto", "next": "fa"})
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    assert result["initial"] == "auto"
    assert [option["value"] for option in result["options"]] == ["auto", "en", "ru", "fa", "zh"]
    assert result["options"][0]["text"] == catalog["language.auto"]
    assert catalog["language.hint"] in result["text"]
    assert result["writes"] == [{"language": "fa"}]


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
@pytest.mark.parametrize("next_language", ["ru", None])
def test_person_language_select_saves_only_the_changed_override(locale: str, next_language: str | None) -> None:
    result = run_console("person-language", locale, {"user": {"language": "fa"}, "next": next_language})
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    assert result["found"], "person settings need a language select"
    assert result["initial"] == "fa" and result["options"] == ["default", "en", "ru", "fa", "zh"]
    assert catalog["language.person"] in result["text"]
    assert result["dirty"] is True and result["afterSaveDirty"] is False
    assert result["writes"] == [{"language": next_language}]
    assert result["afterSave"] == (next_language or "default")
    assert result["stored"] == []


def test_untouched_person_language_does_not_create_a_patch() -> None:
    result = run_console("person-language", data={"user": {"language": "fa"}, "next": "fa"})
    assert result["found"] and result["writes"] == [] and result["dirty"] is False


def test_failed_person_language_save_keeps_the_draft() -> None:
    result = run_console("person-language", data={"user": {"language": None}, "next": "fa", "failed": True})
    assert result["found"] and result["afterSave"] == "fa" and result["afterSaveDirty"] is True


def test_person_language_draft_survives_passive_refresh_with_focus() -> None:
    result = run_console("person-live-language", data={"user": {"language": "fa"}, "next": "ru", "remote": "zh"})
    assert result["found"] and result["sameSelect"] and result["focusKept"]
    assert result["afterRefresh"] == "ru" and result["writes"] == [{"language": "ru"}]


@pytest.mark.parametrize(
    ("locale", "title", "status"),
    [
        ("en", "Connection Setup", "Opening your connection page…"),
        ("ru", "Настройка подключения", "Открываем вашу страницу подключения…"),
        ("fa", "تنظیم اتصال", "در حال باز کردن صفحهٔ اتصال شما…"),
        ("zh", "连接设置", "正在打开您的连接页面…"),
    ],
)
def test_support_text_matches_saved_choice_before_its_main_script_loads(locale: str, title: str, status: str) -> None:
    result = run_console("delivery-support-paint", locale, {"language": "ru" if locale != "ru" else "en"})
    assert result["title"] == title and result["status"] == status
    assert result["lang"] == locale and result["dir"] == ("rtl" if locale == "fa" else "ltr")
    assert result["requests"] == 0
