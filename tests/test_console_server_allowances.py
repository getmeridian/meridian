"""The Servers screen shows reviewed billing evidence with a local edit action."""

from __future__ import annotations

import json

import pytest

from meridian.control.ui_routes import UI_DIR
from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_server_allowance_cards_show_usage_payment_attention_and_the_edit_action(locale: str) -> None:
    usage = {
        "server_ref": "srv-a",
        "node_id": "main",
        "measurement": "accepted_user_traffic",
        "metadata": {
            "allowance_bytes": 1024**3,
            "reset_day": 31,
            "provider": "<img src=x onerror='window.pwned=1'>",
            "pay_on": "2026-10-31",
            "pay_every": "month",
        },
        "used_bytes": 1024**3,
        "lifetime_bytes": 2 * 1024**3,
        "period_start": "2026-10-31T00:00:00Z",
        "period_end": "2026-11-30T00:00:00Z",
        "counting_since": "2026-11-01T00:00:00Z",
        "next_payment_on": "2026-11-30",
    }
    result = run_console(
        "servers",
        locale,
        {
            "server_usage": [usage],
            "server_attention": [
                {
                    "server_ref": "srv-a",
                    "reasons": ["allowance_exceeded", "payment_due"],
                    "usage": usage,
                }
            ],
        },
    )
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    assert catalog["presentation.billing_edit"] in result["text"]
    for key in (
        "server_allowance.approximate",
        "server_allowance.since",
        "server_allowance.allowance_exceeded",
        "server_allowance.payment_due",
    ):
        assert catalog[key].split("{")[0] in result["text"]
    assert "2026-11-30" in result["text"] and "2026-10-31" in result["text"]
    assert result["allowanceLocalized"] and result["periodIsolated"]
    assert "<img" in result["text"] and result["images"] == 0 and not result["pwned"]
    assert all(event["method"] == "GET" and event["url"] == "ui/api/fleet" for event in result["requests"])


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_relay_payment_cards_say_not_measured(locale: str) -> None:
    usage = {
        "server_ref": "srv-relay",
        "node_id": None,
        "measurement": "not_measured",
        "metadata": {
            "allowance_bytes": 1024**4,
            "reset_day": 1,
            "provider": "Example provider",
            "pay_on": "2026-11-15",
            "pay_every": None,
        },
        "used_bytes": None,
        "lifetime_bytes": None,
        "period_start": None,
        "period_end": None,
        "counting_since": None,
        "next_payment_on": "2026-11-15",
    }
    result = run_console(
        "servers",
        locale,
        {
            "servers": [],
            "server_usage": [usage],
            "server_attention": [
                {
                    "server_ref": "srv-relay",
                    "reasons": ["payment_overdue"],
                    "usage": usage,
                }
            ],
        },
    )
    assert "srv-relay" in result["text"]
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    assert catalog["presentation.billing_edit"] in result["text"]
    assert catalog["server_allowance.not_measured"] in result["text"]
    assert catalog["server_allowance.payment_overdue"] in result["text"]
    assert "0 B" not in result["text"]
