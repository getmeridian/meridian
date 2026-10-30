"""Localized owner contact stays plain text and makes no outside requests."""

from __future__ import annotations

import json

import pytest

from meridian.render import render_apps_catalog, render_config_json
from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom
TEXT = '<img src=x onerror="window.pwned=1"> Ask for help'
LABELS = {"en": "Contact owner", "ru": "Связаться с владельцем", "fa": "تماس با مالک", "zh": "联系所有者"}


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
@pytest.mark.parametrize("mode", ["owner-page", "owner-support"])
@pytest.mark.parametrize(
    "link", ["https://example.com/help", "tg://resolve?domain=sample_support", "mailto:help@example.com"]
)
def test_contact_is_localized_safe_and_user_initiated(locale: str, mode: str, link: str) -> None:
    config = json.loads(
        render_config_json([], "198.51.100.1", subscription_url="https://198.51.100.1/connect/sub/demo")
    )
    contact = {"text": TEXT, "link": link}
    config["owner_contact"] = contact
    result = run_console(
        mode, locale, {"config": config, "catalog": json.loads(render_apps_catalog()), "contact": contact}
    )
    assert TEXT in result["text"] and LABELS[locale] in result["text"]
    assert result["contactLink"] == link
    assert "noreferrer" in result["rel"] and result["target"] == "_blank"
    assert result["injected"] is False
    assert result["dir"] == ("rtl" if locale == "fa" else "ltr")
    assert result["externalRequests"] == []
    if mode == "owner-support":
        assert result["pageLink"].endswith("/sub/sampleShortUuid00")


@pytest.mark.parametrize("mode", ["owner-page", "owner-support"])
def test_malformed_contact_cannot_create_an_active_link(mode: str) -> None:
    config = json.loads(render_config_json([], "198.51.100.1"))
    contact = {"text": "Ask for help", "link": "javascript:alert(1)"}
    config["owner_contact"] = contact
    result = run_console(
        mode, "en", {"config": config, "catalog": json.loads(render_apps_catalog()), "contact": contact}
    )
    assert not result["contactLink"]
    assert result["externalRequests"] == []


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_console_shows_contact_with_the_reviewed_cli_command(locale: str) -> None:
    result = run_console("owner-settings", locale, {"contact": {"text": TEXT, "link": "mailto:help@example.com"}})
    assert TEXT in result["text"] and "mailto:help@example.com" in result["text"]
    assert "meridian fleet contact" in result["text"]
    assert result["controls"] == 0
    assert result["injected"] is False
