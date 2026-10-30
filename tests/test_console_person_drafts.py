"""Person/password drafts guard navigation and dismissal and survive live status refresh."""

import json

import pytest

from meridian.control.ui_routes import UI_DIR
from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


@pytest.mark.parametrize("screen", ["access", "metadata", "password"])
@pytest.mark.parametrize("navigation", ["tab", "hash", "back"])
def test_dirty_forms_offer_stay_then_leave_without_losing_the_draft(screen: str, navigation: str) -> None:
    result = run_console("person-guard", data={"screen": screen, "navigation": navigation})
    assert result["prompt"] is True and result["stayPreserved"] is True
    assert result["secondPrompt"] is True and result["left"] is True
    assert result["stored"] == []


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_leave_stay_feedback_is_localized(locale: str) -> None:
    result = run_console("person-guard", locale, {"screen": "metadata", "navigation": "tab"})
    catalog = json.loads((UI_DIR / f"locales/{locale}.json").read_text())
    assert catalog["draft.title"] in result["promptText"]
    assert catalog["draft.leave"] in result["promptText"] and catalog["draft.stay"] in result["promptText"]


@pytest.mark.parametrize("screen", ["access", "metadata", "password"])
@pytest.mark.parametrize("revert", [False, True])
def test_pristine_or_reverted_forms_navigate_without_a_prompt(screen: str, revert: bool) -> None:
    result = run_console("person-guard", data={"screen": screen, "navigation": "tab", "clean": True, "revert": revert})
    assert result["prompt"] is False and result["left"] is True


def test_reverting_expiry_and_quota_to_unlimited_ignores_disabled_draft_fields() -> None:
    result = run_console(
        "person-guard",
        data={
            "screen": "access",
            "navigation": "tab",
            "cycleUnlimited": True,
            "user": {"expire_at": None, "traffic_limit_bytes": 0},
        },
    )
    assert result["prompt"] is False and result["left"] is True


def test_saving_reverted_unlimited_values_does_not_patch_unchanged_limits() -> None:
    result = run_console(
        "person-guard",
        data={
            "screen": "access",
            "navigation": "tab",
            "cycleUnlimited": True,
            "saveCycle": True,
            "user": {"expire_at": None, "traffic_limit_bytes": 0},
        },
    )
    assert result["writes"] == []


def test_confirmed_removal_discards_the_deleted_persons_draft_without_another_prompt() -> None:
    result = run_console("person-mutation", data={"action": "remove", "editBeforeRemove": True})
    assert result["hash"] == "#/people" and result["asks"] == 0


def test_first_signed_in_navigation_keeps_a_guardable_history_entry() -> None:
    result = run_console("person-first-navigation")
    assert result["prompt"] is True and result["restored"] is True


def test_signing_out_guards_an_edited_person_form() -> None:
    result = run_console("person-guard", data={"screen": "metadata", "navigation": "signout"})
    assert result["prompt"] is True and result["stayPreserved"] is True and result["left"] is True


def test_initial_person_failure_stops_loading_and_offers_a_working_retry() -> None:
    result = run_console("person-initial-failure")
    assert result["spinner"] is False and result["retry"] is True and result["recovered"] is True


@pytest.mark.parametrize("kind", ["add", "password"])
@pytest.mark.parametrize("dismissal", ["close", "escape", "cancel"])
def test_dirty_person_and_password_dialogs_guard_dismissal(kind: str, dismissal: str) -> None:
    result = run_console("person-guard-dialog", data={"kind": kind, "dismissal": dismissal})
    assert result["prompt"] is True and result["stayPreserved"] is True
    assert result["left"] is True and result["writes"] == []


@pytest.mark.parametrize("failed", [False, True])
def test_saving_clears_only_successful_drafts_and_browser_close_guard(failed: bool) -> None:
    result = run_console("person-guard-save", data={"failed": failed})
    assert result["beforeUnload"] is True
    assert result["afterUnload"] is failed
    assert result["prompt"] is failed


def test_person_refresh_updates_status_usage_and_preserves_edited_inputs_and_focus() -> None:
    result = run_console("person-live")
    assert result["sameNote"] is True and result["sameTime"] is True and result["focusKept"] is True
    assert result["note"] == "Private draft" and result["time"] == "12:34:56"
    catalog = json.loads((UI_DIR / "locales/en.json").read_text())
    assert result["status"] == catalog["status.limited"] and "1 GiB" in result["usage"]
    assert result["passive"] == [False, True]
    assert result["readsWhileHidden"] == 0 and result["readsAfterStop"] == 0
    assert result["stored"] == []


def test_an_older_live_read_cannot_replace_a_completed_mutation() -> None:
    result = run_console("person-live-race")
    assert result["status"] == "Active"


def test_saving_one_person_form_keeps_another_forms_draft() -> None:
    result = run_console("person-guard-save", data={"twoForms": True})
    assert result["otherDraft"] == "Private draft"
    assert result["afterUnload"] is True


@pytest.mark.parametrize("action", ["cap", "clear"])
def test_device_actions_remain_on_the_person_card_that_started_them(action: str) -> None:
    result = run_console("person-device-race", data={"action": action})
    assert result["after"] == result["before"]
    assert "Sample person" in result["toasts"][-1]
