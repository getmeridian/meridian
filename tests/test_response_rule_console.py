"""The subscription tester shows app-rule decisions as localized, transient plain text."""

from __future__ import annotations

import pytest

from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
@pytest.mark.parametrize("blocked", [False, True])
def test_console_preview_names_the_custom_rule_and_distinguishes_a_refusal(locale: str, blocked: bool) -> None:
    decision = {
        "format": None if blocked else "singbox",
        "block": blocked,
        "selection": {"rule": "custom", "name": "happ", "number": 1, "suffix": ""},
        "action": "block" if blocked else "format",
        "reason": "first_custom_match",
        "matched_conditions": [{"header": "user-agent", "operator": "prefix"}],
        "headers": {},
    }
    result = run_console(
        "delivery-format-preview",
        locale,
        {
            "response": {
                **decision,
                "response_rule": decision,
                "response_kind": "blocked" if blocked else "subscription",
                "included": [],
                "omitted": [],
                "protocols": [],
                "response_headers": {},
                "placeholder": None,
            }
        },
    )
    assert "happ" in result["text"]
    assert "preview.rule_custom" not in result["text"] and "preview.format_null" not in result["text"]
    assert result["stored"] == 0 and not result["injected"]


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_console_tester_can_read_fleet_defaults_without_a_person(locale: str) -> None:
    result = run_console(
        "delivery-format-preview",
        locale,
        {
            "person": "",
            "user_agent": "Hiddify/2",
            "response": {
                "format": "singbox",
                "block": False,
                "response_kind": "subscription",
                "selection": {"rule": "user_agent", "family": "singbox", "suffix": ""},
                "reason": "built_in_selector",
                "action": "format",
                "matched_conditions": [],
                "headers": {},
            },
        },
    )
    assert result["writes"] == [{"user_agent": "Hiddify/2", "accept": "", "suffix": ""}]
    assert result["error"] == "" and result["stored"] == 0 and not result["injected"]
