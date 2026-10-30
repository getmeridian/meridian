"""Settings confirms service-rendered literal notices in every console locale."""

from __future__ import annotations

import json

import pytest

from meridian.control.ui_routes import UI_DIR
from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_notice_settings_confirms_the_rendered_preview_without_html_or_storage(locale: str) -> None:
    text = '<img src=x onerror="window.pwned=1"> Hello {name}'
    result = run_console("delivery-notice", locale, {"text": text, "person": "person"})
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    assert catalog["notice.title"] in result["text"]
    assert result["preview"] == text.replace("{name}", "person")
    assert result["confirmations"][0]["text"] == result["preview"]
    assert [write["confirm"] for write in result["writes"]] == [False, True]
    assert result["injected"] is False and result["stored"] == 0


@pytest.mark.parametrize("action", ["preview", "clear"])
def test_notice_preview_and_declined_clear_do_not_save(action: str) -> None:
    result = run_console(
        "delivery-notice", data={"notice": {"text": "Current"}, "text": "Next", "action": action, "confirm": False}
    )
    assert result["writes"][0]["confirm"] is False and len(result["writes"]) == 1


def test_notice_clear_ignores_unfinished_text_and_the_preview_person() -> None:
    result = run_console(
        "delivery-notice",
        data={"notice": {"text": "Current"}, "text": "Invalid {ip}", "person": "missing", "action": "clear"},
    )
    assert len(result["writes"]) == 2
    assert all(write["notice"] is None and write["for_user"] is None for write in result["writes"])


@pytest.mark.parametrize("text", ["x" * 501, "Two\nparagraphs", "Unknown {ip}", "{name.__class__}"])
def test_notice_settings_explains_bad_text_before_requesting_a_preview(text: str) -> None:
    result = run_console("delivery-notice", data={"text": text})
    assert result["error"] and result["writes"] == [] and result["confirmations"] == []


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_page_notice_is_plain_text_and_does_not_request_or_store_anything(locale: str) -> None:
    result = run_console("delivery-page-notice", locale, {"notice": {"text": "<img src=x> پیام 测试", "until": None}})
    assert "<img src=x> پیام 测试" in result["text"] and result["hidden"] is False
    assert result["injected"] is False and result["requests"] == result["stored"] == 0


@pytest.mark.parametrize(
    "notice", [None, {"text": "Expired", "until": "2020-01-01T00:00:00Z"}, {"text": "x" * 1025}, {"text": "Bad\ntext"}]
)
def test_page_notice_hides_expired_or_untrusted_values(notice: object) -> None:
    result = run_console("delivery-page-notice", data={"notice": notice})
    assert result["hidden"] is True and result["text"] == ""


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_settings_edits_the_fleet_subscription_language_in_its_own_card(locale: str) -> None:
    result = run_console("delivery-language", locale, {"language": "en", "next": "fa"})
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    assert result["initial"] == "en" and result["writes"] == [{"language": "fa"}]
    assert catalog["language.title"] in result["text"]


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_settings_format_preview_is_localized_plain_text_and_transient(locale: str) -> None:
    result = run_console(
        "delivery-format-preview",
        locale,
        {
            "user_agent": "Hiddify/2",
            "accept": "application/json",
            "response": {
                "format": "singbox",
                "response_kind": "subscription",
                "selection": {"rule": "user_agent", "family": "singbox", "suffix": ""},
                "protocols": ["reality"],
                "placeholder": None,
                "included": [{"remark": '<img src=x onerror="window.pwned=1"> Reality', "protocol": "reality"}],
                "omitted": [
                    {"remark": "XHTTP", "protocol": "xhttp", "reason": "unsupported_format", "restriction": "protocol"}
                ],
                "response_headers": {"profile-web-page-url": "[redacted]"},
            },
        },
    )
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    assert catalog["preview.reason_unsupported_format"] in result["text"]
    assert '<img src=x onerror="window.pwned=1"> Reality' in result["text"]
    assert result["writes"] == [
        {"username": "person", "user_agent": "Hiddify/2", "accept": "application/json", "suffix": ""}
    ]
    assert result["injected"] is False and result["stored"] == 0


@pytest.mark.parametrize("data", [{"user_agent": "x" * 513}, {"accept": "x" * 257}])
def test_settings_format_preview_rejects_bad_headers_before_request(data: dict) -> None:
    result = run_console("delivery-format-preview", data=data)
    assert result["error"] and result["writes"] == []
