"""Browser-local modes keep deep links, account controls, and drafts usable."""

import pytest

from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_a_fresh_browser_lands_on_overview_in_simple_mode(locale: str) -> None:
    result = run_console("shell-default", locale)
    assert result["mode"] == "simple"
    assert result["route"]["name"] == "overview"
    assert result["tabs"] == ["overview", "people", "servers", "settings"]
    assert result["brandHref"] == "#/overview"
    assert "Meridian" in result["brand"] and "Sample VPN" in result["brand"]
    assert result["storedMode"] is None
    assert result["dir"] == ("rtl" if locale == "fa" else "ltr")


def test_switching_modes_keeps_a_shared_route_and_survives_a_reload() -> None:
    result = run_console("shell-switch", data={"hash": "#/people?status=disabled"})
    assert result["mode"] == "detailed" and result["storedMode"] == "detailed"
    assert result["hash"] == "#/people?status=disabled"
    assert result["tabs"] == ["overview", "people", "servers", "revisions", "tokens", "settings"]
    assert result["events"] == ["detailed"]
    reloaded = run_console("shell-default", data={"storage": {"mrd.mode": result["storedMode"]}})
    assert reloaded["mode"] == "detailed"


@pytest.mark.parametrize("screen", ["revisions/2", "tokens"])
def test_a_detailed_deep_link_opens_in_simple_with_a_one_click_switch(screen: str) -> None:
    result = run_console("shell-deep-link", data={"hash": "#/" + screen})
    assert result["beforeMode"] == "simple"
    assert result["beforeScreen"] == screen.split("/")[0]
    assert result["note"] == "Shown in Detailed view"
    assert result["mode"] == "detailed" and result["hash"] == "#/" + screen
    assert result["noteAfter"] is None


def test_switching_to_simple_from_a_detailed_screen_goes_to_overview() -> None:
    result = run_console(
        "shell-switch", data={"hash": "#/tokens", "storage": {"mrd.mode": "detailed"}, "choose": "simple"}
    )
    assert result["mode"] == "simple" and result["route"]["name"] == "overview"


def test_mode_and_theme_changes_preserve_a_person_draft_and_focus() -> None:
    result = run_console("shell-draft", data={"hash": "#/people/sample"})
    assert result["sameInput"] and result["focusKept"]
    assert result["draft"] == "Unsaved text" and result["unsaved"]
    assert result["leavePrompts"] == 0 and result["screenDraws"] == 1
    assert result["theme"] == "dark" and result["mode"] == "detailed"
    assert result["writes"] == []


def test_the_account_menu_owns_theme_language_and_sign_out() -> None:
    result = run_console("shell-account")
    assert result["themes"] == ["auto", "light", "dark"]
    assert result["languages"] == ["en", "ru", "fa", "zh"]
    assert result["signOutInside"] and result["closedOnEscape"]
    assert result["theme"] == "dark" and result["language"] == "fa" and result["dir"] == "rtl"
    assert result["storedTheme"] == "dark" and result["storedLanguage"] == "fa"


@pytest.mark.parametrize("stored", ["broken", "null", "Detailed"])
def test_an_invalid_saved_mode_uses_simple(stored: str) -> None:
    assert run_console("shell-default", data={"storage": {"mrd.mode": stored}})["mode"] == "simple"


def test_mode_controls_work_when_browser_storage_is_unavailable() -> None:
    result = run_console("shell-switch", data={"noStorage": True})
    assert result["mode"] == "detailed" and result["tabs"][3:5] == ["revisions", "tokens"]


@pytest.mark.parametrize("locale", ["en", "fa"])
def test_mode_switch_has_radio_keyboard_navigation(locale: str) -> None:
    result = run_console("shell-keyboard", locale)
    assert result["mode"] == "detailed" and result["keyboardFocus"] == "detailed"
    assert result["radioStates"] == ["false", "true"]


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_simple_disclosures_keep_capacity_open_across_passive_refresh(locale: str) -> None:
    result = run_console("shell-server-disclosure", locale, {"hash": "#/servers/main"})
    assert result["initialOpen"] is False and result["afterPollOpen"] is True
    assert result["capacityNote"] is None and result["misleadingDisclosureCaptions"] == 0
    assert result["targetServer"] == "main"
    assert result["moreStayedOpen"]


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_simple_server_cards_keep_host_facts_in_more_across_modes_and_refresh(locale: str) -> None:
    result = run_console("shell-server-facts", locale, {"hash": "#/servers"})
    assert result["hostInitiallyHidden"] and result["hostHiddenAgain"]
    assert result["healthFactsVisible"] and result["statusChips"] == 3
    assert result["hostAvailableInMore"] and result["hostDetailedOutsideMore"]
    assert result["sameCard"] and result["hostMoreAfterPoll"] and result["moreAfterPollOpen"]
    assert result["restartInMenu"] and result["writes"] == []


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_server_management_guidance_is_one_caption_with_a_copyable_command(locale: str) -> None:
    result = run_console("shell-cli-hint", locale, {"hash": "#/servers"})
    assert result["hintParagraphs"] == 1 and result["hintTitle"]
    assert result["hintCommand"] == ["meridian"] and result["hintCopyCount"] == 1
    assert result["writes"] == []


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_settings_moves_appearance_and_discloses_advanced_sections_without_losing_input(locale: str) -> None:
    result = run_console("shell-settings", locale, {"hash": "#/settings"})
    assert result["appearanceCards"] == 0
    assert result["advancedInitiallyClosed"]
    assert result["samePreviewInput"] and result["previewValue"] == "sample"
    assert result["advancedAfterDetailedOpen"]
    assert result["misleadingDisclosureCaptions"] == 0
