"""Tokens and Revisions keep their reviews inside compact, accessible lists."""

from __future__ import annotations

import json
from typing import Any

import pytest

from meridian.control.token_capabilities import capability_catalog
from meridian.control.ui_routes import UI_DIR
from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom
LOCALES = ("en", "ru", "fa", "zh")


def run(scenario: str, locale: str = "en", **data: Any) -> dict[str, Any]:
    return run_console("records-" + scenario, locale, data)


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("screen", ["tokens", "revisions"])
@pytest.mark.parametrize("detailed", [False, True])
def test_empty_token_and_revision_lists_explain_the_next_step_in_both_modes(
    locale: str, screen: str, detailed: bool
) -> None:
    result = run("empty", locale, screen=screen, detailed=detailed, empty=True)
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    assert result["emptyTitle"] == catalog[f"{screen}.empty"]
    assert result["emptyCaption"] == catalog[f"{screen}.empty_hint"]
    assert result["emptyStatus"] == "status" and result["hasCreate"] is (screen == "tokens")
    assert result["note"] is (not detailed) and result["writes"] == []


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("screen", ["tokens", "revisions"])
def test_record_rows_have_isolated_names_status_key_facts_and_contextual_menus(locale: str, screen: str) -> None:
    result = run("rows", locale, screen=screen, detailed=True)
    assert result["semanticList"] and len(result["rows"]) == (3 if screen == "tokens" else 4)
    assert all(row["nameIsolated"] and row["status"] and row["facts"] and row["menu"] for row in result["rows"])
    assert all(row["actionsOnlyInMenu"] and row["menuName"] for row in result["rows"])
    assert result["textOnly"] and result["outside"] == [] and result["writes"] == []
    if screen == "tokens":
        assert [row["kind"] for row in result["rows"]] == ["success", "warn", "neutral"]
        assert [row["dim"] for row in result["rows"]] == [False, True, True]
        assert result["rows"][-1]["canRevoke"] is False
    else:
        assert [row["kind"] for row in result["rows"]] == ["success", "danger", "neutral", "neutral"]
        assert all(row["mainLink"] and row["diffLink"] for row in result["rows"])


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("screen", ["tokens", "revisions"])
@pytest.mark.parametrize("width", [1040, 390])
def test_record_lists_share_phone_cards_large_menu_targets_and_dark_rtl_tokens(
    locale: str, screen: str, width: int
) -> None:
    result = run("layout", locale, screen=screen, width=width, dark=True, detailed=True)
    assert result["theme"] == "dark" and result["surface"] == "#252836"
    assert result["dir"] == ("rtl" if locale == "fa" else "ltr")
    assert all(target >= 44 for target in result["targets"])
    assert result["menuOverflow"] == "visible"
    if width == 390:
        assert result["phoneCards"] and result["menuBounded"]


@pytest.mark.parametrize("locale", LOCALES)
def test_token_permissions_are_read_only_and_menus_close_on_escape_and_outside_click(locale: str) -> None:
    result = run("permissions", locale, screen="tokens", detailed=True)
    labels = {row.capability: row.labels[locale] for row in capability_catalog().capabilities}
    assert result["permissions"] == [labels["people.read"], labels["people.private"]]
    assert result["escapeClosed"] and result["focusReturned"] and result["outsideClosed"]
    assert result["dialogFocusReturned"]
    assert result["menuClosedForDialog"] and result["writes"] == [] and result["storedSecrets"] is False


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("cancel", [False, True])
def test_token_revocation_keeps_the_existing_confirmation_and_never_revokes_before_consent(
    locale: str, cancel: bool
) -> None:
    result = run("revoke", locale, screen="tokens", detailed=True, cancel=cancel)
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    assert result["beforeConsent"] == [] and result["review"] == catalog["tokens.revoke_text"]
    assert result["menuClosedForDialog"]
    assert result["writes"] == (
        [] if cancel else [{"method": "DELETE", "url": "api/tokens/000000000001", "body": None}]
    )
    assert result["revoked"] is (not cancel) and result["storedSecrets"] is False


@pytest.mark.parametrize("locale", LOCALES)
def test_revision_row_menus_open_the_existing_diff_and_late_comparisons_cannot_replace_it(locale: str) -> None:
    result = run("diff", locale, screen="revisions", detailed=True)
    assert result["hash"] == "#/revisions/4" and result["compare"] == "2"
    assert result["afterNewest"] == result["afterOlder"] and result["hasDiff"]
    assert result["writes"] == [] and result["outside"] == []
