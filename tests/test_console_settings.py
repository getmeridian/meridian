"""Settings sections use the shipped shell, guarded forms, and local disclosures."""

from __future__ import annotations

import json
from typing import Any

import pytest

from meridian.control.ui_routes import UI_DIR
from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom
LOCALES = ("en", "ru", "fa", "zh")


def run(scenario: str, locale: str = "en", **data: Any) -> dict[str, Any]:
    return run_console("settings-" + scenario, locale, data)


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("section", ["", "general", "notice", "language", "privacy", "security", "advanced", "unknown"])
def test_settings_fragments_open_one_labelled_section(locale: str, section: str) -> None:
    result = run("routes", locale, section=section, detailed=True)
    expected = section if section in {"notice", "language", "privacy", "security", "advanced"} else "general"
    assert result["selected"] == expected
    assert result["visiblePanels"] == [expected]
    assert result["linkedPanels"] and result["selectedTabs"] == [expected]
    assert result["tabStops"] == [expected]
    assert result["writes"] == [] and result["outside"] == []
    assert result["dir"] == ("rtl" if locale == "fa" else "ltr")


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("width", [1040, 390])
def test_settings_subnavigation_is_a_desktop_column_and_a_scrolling_phone_chip_row(locale: str, width: int) -> None:
    result = run("layout", locale, width=width)
    assert result["tabs"] == ["general", "notice", "language", "privacy", "security"]
    assert result["orientation"] == ("horizontal" if width == 390 else "vertical")
    assert all(target >= 44 for target in result["targets"])
    assert result["layout"] == ("flex" if width == 390 else "grid")
    assert result["navDirection"] == ("row" if width == 390 else "column")
    if width == 390:
        assert result["overflow"] == "auto" and result["wrap"] == "nowrap"


@pytest.mark.parametrize("locale", LOCALES)
def test_settings_keyboard_navigation_matches_direction_and_keeps_focus_on_the_selected_tab(locale: str) -> None:
    result = run("keyboard", locale, width=390, detailed=True)
    assert result["afterArrow"] == ("advanced" if locale == "fa" else "notice")
    assert result["afterHome"] == "general" and result["afterEnd"] == "advanced"
    assert result["afterUp"] == "security" and result["focusMatches"]
    assert result["sameNavigation"] and result["writes"] == []


@pytest.mark.parametrize("locale", LOCALES)
def test_advanced_deep_links_open_in_simple_with_the_shared_mode_note(locale: str) -> None:
    result = run("advanced", locale, section="advanced")
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    assert result["beforeMode"] == "simple" and result["beforeSection"] == "advanced"
    assert result["note"] == catalog["mode.shown_detailed"] and result["notes"] == 1
    assert result["beforeTabs"] == ["general", "notice", "language", "privacy", "security", "advanced"]
    assert result["mode"] == "detailed" and result["selected"] == "advanced" and result["noteAfter"] == 0
    assert result["samePreview"] and result["routing"] and result["responseRules"]


@pytest.mark.parametrize("section", ["notice", "language", "privacy", "security", "advanced"])
def test_every_settings_draft_guards_switching_sections_and_leave_discards_only_the_old_section(section: str) -> None:
    result = run("draft", section=section, detailed=True)
    assert result["dirty"] and result["prompt"] and result["stayPreserved"]
    assert result["currentDoesNotPrompt"] and result["secondPrompt"]
    assert result["selected"] == "general" and result["discarded"]
    assert result["writes"] == [] and result["storedDraft"] is False


@pytest.mark.parametrize("navigation", ["hash", "back"])
def test_settings_fragment_and_history_changes_use_the_same_draft_guard(navigation: str) -> None:
    result = run("draft", section="notice", navigation=navigation, detailed=True)
    assert result["prompt"] and result["stayPreserved"] and result["secondPrompt"]
    assert result["selected"] == "general" and result["discarded"]


@pytest.mark.parametrize("section", ["notice", "language", "privacy", "security", "advanced"])
def test_settings_mode_and_theme_changes_keep_shared_drafts_inputs_and_focus(section: str) -> None:
    result = run("preserve", section=section)
    assert result["sameField"] and result["focusKept"] and result["dirty"]
    assert result["sameNavigation"] and result["sameReads"] and result["prompts"] == 0
    assert result["mode"] == "detailed" and result["theme"] == "dark"


def test_leaving_advanced_for_simple_waits_for_the_draft_decision() -> None:
    result = run("advanced-mode", section="advanced", detailed=True)
    assert result["prompt"] and result["stayed"] and result["secondPrompt"]
    assert result["mode"] == "simple" and result["hash"] == "#/overview"


@pytest.mark.parametrize("section", ["notice", "language", "privacy", "security"])
def test_untouched_settings_forms_do_not_guard_navigation_after_their_initial_reads(section: str) -> None:
    result = run("clean", section=section, detailed=True)
    assert result["dirty"] is False and result["prompts"] == 0 and result["selected"] == "general"


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("lowering", ["private", "history-off"])
def test_lowering_privacy_preserves_saved_numbers_when_unfinished_edits_are_invalid(locale: str, lowering: str) -> None:
    result = run("lowering", locale, section="privacy", lowering=lowering, fullPrivacy=True)
    assert result["beforeConfirm"] == [] and result["review"]
    assert result["error"] == "" and result["dirtyAfter"] is False
    expected = {
        "device_limits": "private" if lowering == "private" else "full",
        "default_device_limit": 3,
        "usage_history": "daily" if lowering == "private" else "off",
        "history_days": 90,
    }
    assert len(result["writes"]) == 2 and all(write["body"] == expected for write in result["writes"])
    assert result["stepUps"] == 1 and result["disclosuresKept"]


@pytest.mark.parametrize(
    ("section", "cancel"),
    [
        ("notice", False),
        ("language", False),
        ("privacy", False),
        ("security", False),
        ("notice", True),
        ("privacy", True),
    ],
)
def test_settings_saves_reset_drafts_and_cancelled_reviews_keep_them(section: str, cancel: bool) -> None:
    result = run("save", section=section, cancel=cancel)
    assert result["dirtyBefore"]
    assert result["dirtyAfter"] is cancel
    if section in {"notice", "privacy"}:
        assert result["review"] and result["mutationsBeforeConfirm"] == 0
    assert result["mutations"] == (0 if cancel else 1)
    if section in {"privacy", "security"} and not cancel:
        assert result["stepUps"] == 1


def test_clearing_a_notice_ignores_invalid_unfinished_text_and_expiry() -> None:
    result = run("clear", section="notice")
    assert result["review"] and result["error"] == "" and result["dirtyAfter"] is False
    assert [write["body"]["notice"] for write in result["writes"]] == [None, None]
    assert [write["body"]["confirm"] for write in result["writes"]] == [False, True]


@pytest.mark.parametrize("locale", LOCALES)
def test_general_has_copyable_commands_and_security_keeps_every_disclosure(locale: str) -> None:
    result = run("disclosures", locale)
    assert result["generalEditable"] == 0 and len(result["commands"]) == 4
    assert result["contactIsText"] and result["injected"] is False
    assert result["securityDisclosures"] and result["privacyDisclosures"]
    assert result["learnMore"] >= 2 and result["writes"] == []


@pytest.mark.parametrize("locale", LOCALES)
def test_a_phone_settings_link_scrolls_its_section_chip_into_view(locale: str) -> None:
    """The chip row overflows on phones, so a deep link to a late section once left its chip cut off at the edge."""
    result = run("chip-scroll", locale, width=390, section="security")
    assert result["initial"] == {"section": "security", "inline": "nearest", "block": "nearest"}
    assert result["afterRoute"]["section"] == "notice"
