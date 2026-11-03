"""Person summaries, fragment tabs, and inline drafts use the shipped console."""

from datetime import datetime
from pathlib import Path

import pytest

from meridian.control.account_schedule_models import UserSchedulePreviewRequest
from meridian.control.models import UserCreate
from meridian.control.service import ControlService
from meridian.core.people_bulk import BulkAction, BulkPreviewRequest
from tests.support.console_dom import needs_console_dom, run_console
from tests.support.control_service import FLEET_ID

pytestmark = needs_console_dom
# The stub refuses what the real preview request forbids, so a console body cannot drift past it.
PREVIEW_FIELDS = sorted(UserSchedulePreviewRequest.model_fields)


@pytest.fixture(autouse=True)
def utc_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "UTC")


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_person_summary_keeps_actions_and_exact_dates_outside_the_tab_panels(locale: str) -> None:
    result = run_console("person-tabs-header", locale)
    assert result["summary"] and result["back"] and result["name"] == "Sample person"
    assert result["tag"] == "GROUP_1" and result["status"]
    assert result["meter"] == "51" and result["reset"]
    assert result["expiryFocusable"] and "2026-12-02T12:00:00Z" in result["exactExpiry"]
    assert result["actions"] == ["share", "extend", "toggle"]
    assert result["pageQr"] == 0 and result["dialogQr"] == 1
    assert result["shareActions"] and result["connections"]
    assert result["dir"] == ("rtl" if locale == "fa" else "ltr")
    assert result["outside"] == []


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_summary_expiry_shares_the_reset_meta_line_and_keeps_its_exact_date(locale: str) -> None:
    result = run_console("person-tabs-header", locale)
    assert result["meta"]["together"] and result["meta"]["resetFirst"]
    assert result["reset"] + " · " in result["meta"]["text"]
    assert result["expiryFocusable"] and "2026-12-02T12:00:00Z" in result["exactExpiry"]


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
@pytest.mark.parametrize("public", [True, False])
def test_share_dialog_has_one_heading_and_unframed_content_with_its_caption(locale: str, public: bool) -> None:
    result = run_console("person-tabs-header", locale, {"share": {} if public else {"subscription_url": None}})
    assert result["dialogHeadings"] == 1 and result["dialogCards"] == 0
    assert result["shareCaption"] and result["connections"]
    if public:
        assert result["shareActions"] and result["dialogQr"] == 1


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_enable_and_disable_are_named_actions_without_pressed_toggle_semantics(locale: str) -> None:
    result = run_console("person-tabs-toggle-action", locale)
    assert all(state["pressed"] is None and state["expectedAction"] and state["status"] for state in result["states"])
    assert result["writes"] == [{"status": "disabled"}, {"status": "active"}]


@pytest.mark.parametrize("locale", ["en", "fa"])
def test_disabling_a_person_offers_one_undo_that_sends_the_opposite_status(locale: str) -> None:
    result = run_console("person-tabs-toggle-undo", locale)
    assert result["undo"] and result["offers"] == 1
    assert result["writes"] == [{"status": "disabled"}, {"status": "active"}]
    assert result["label"]


@pytest.mark.parametrize("tab", ["", "usage", "devices", "routing", "keys"])
def test_person_tab_deep_links_select_one_labelled_panel(tab: str) -> None:
    result = run_console("person-tabs-route", data={"tab": tab, "mode": "detailed"})
    assert result["tabs"] == ["access", "usage", "devices", "routing", "keys"]
    assert result["selected"] == (tab or "access")
    assert result["panels"] == [tab or "access"] and result["labelled"]
    assert result["hash"] == "#/people/sample" + ("/" + tab if tab else "")
    assert result["content"]


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
@pytest.mark.parametrize("tab", ["access", "devices"])
def test_visible_tabs_label_the_person_panel_without_a_duplicate_heading(locale: str, tab: str) -> None:
    result = run_console("person-tabs-headings", locale, {"tab": tab, "mode": "detailed"})
    assert result["initial"]["role"] == "tabpanel" and result["initial"]["label"]
    assert result["initial"]["duplicates"] == 0


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_simple_person_access_keeps_its_heading_and_mode_changes_keep_the_panel_label(locale: str) -> None:
    result = run_console("person-tabs-headings", locale, {"switchModes": True})
    assert result["initial"]["duplicates"] == result["simple"]["duplicates"] == 1
    assert result["detailed"]["duplicates"] == 0
    assert result["initial"]["label"] == result["detailed"]["label"] == result["simple"]["label"]
    assert result["samePanel"]


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
@pytest.mark.parametrize("limited", [True, False], ids=["quota", "unlimited"])
def test_person_usage_keeps_one_summary_meter_and_directional_facts_after_refresh(locale: str, limited: bool) -> None:
    result = run_console(
        "person-tabs-usage-meter",
        locale,
        {
            "tab": "usage",
            "mode": "detailed",
            "clock": True,
            "user": {
                "traffic_limit_bytes": 100 * 1024**3 if limited else 0,
                "period_traffic": {"upload_bytes": 10 * 1024**3, "download_bytes": 41 * 1024**3},
            },
        },
    )
    for snapshot in (result["before"], result["after"]):
        assert snapshot["meters"] == int(limited) and snapshot["panelUsageTexts"] == 0
        assert snapshot["duplicateHeadings"] == 0
        assert snapshot["summaryUsageText"] and snapshot["directional"]
    assert result["summaryMeter"] == ("75" if limited else None)


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_simple_access_is_a_direct_panel_and_detailed_restores_aria_tabs(locale: str) -> None:
    result = run_console("person-tabs-single", locale)
    assert result["before"]["stripHidden"] and result["before"]["tabs"] == 0
    assert result["before"]["role"] == "region" and result["before"]["label"] and result["before"]["content"]
    assert not result["after"]["stripHidden"] and result["after"]["tabs"] == 5
    assert result["after"]["role"] == "tabpanel" and result["after"]["labelled"] and result["after"]["samePanel"]


@pytest.mark.parametrize("locale", ["en", "fa"])
@pytest.mark.parametrize("mode", ["simple", "detailed"])
def test_returning_to_access_focuses_the_panel_when_its_only_tab_disappears(locale: str, mode: str) -> None:
    result = run_console("person-tabs-access-focus", locale, {"tab": "usage", "mode": mode})
    assert not result["hiddenFocus"] and result["stripHidden"] is (mode == "simple")
    assert result["focusedPanel" if mode == "simple" else "focusedTab"] == "access"


@pytest.mark.parametrize("locale", ["en", "fa"])
def test_person_tabs_support_arrow_home_end_and_keep_the_fragment(locale: str) -> None:
    result = run_console("person-tabs-keyboard", locale, {"mode": "detailed"})
    assert result["afterArrow"] == ("keys" if locale == "fa" else "usage")
    assert result["afterHome"] == "access" and result["afterEnd"] == "keys"
    assert result["focused"] == "keys" and result["hash"] == "#/people/sample/keys"
    assert result["backSelected"] == "access" and result["backHash"] == "#/people/sample"


@pytest.mark.parametrize("tab", ["usage", "devices", "routing", "keys"])
def test_simple_person_deep_links_offer_the_detailed_switch(tab: str) -> None:
    result = run_console("person-tabs-simple-link", data={"tab": tab})
    assert result["selected"] == tab and result["note"]
    assert result["switched"] and result["sameSummary"] and result["samePanel"]
    assert result["tabsAfter"] == ["access", "usage", "devices", "routing", "keys"]


def test_simple_more_reaches_metadata_devices_and_removal_without_changing_mode() -> None:
    result = run_console("person-tabs-more")
    assert result["tabs"] == [] and result["metadataClosed"]
    assert result["notes"] and result["tag"]
    assert result["devicesHash"] == "#/people/sample/devices"
    assert result["keysHash"] == "#/people/sample/keys" and result["removal"]
    assert result["mode"] == "simple"


@pytest.mark.parametrize("field", ["expiry", "traffic", "language", "note", "tag"])
def test_person_inline_editors_save_one_field_and_keep_other_drafts(field: str) -> None:
    result = run_console("person-tabs-inline", data={"field": field, "mode": "detailed"})
    assert result["initialForms"] == 0 and result["expanded"]
    assert result["savedClosed"] and result["otherDraft"] == "Other private draft"
    expected = {
        "expiry": {"expire_at": "2032-06-01T12:00:00Z"},
        "traffic": {"traffic_limit_strategy": "week"},
        "language": {"language": "ru"},
        "note": {"note": "Changed private note"},
        "tag": {"tag": "TRIAL_7"},
    }
    assert result["writes"] == [expected[field]]
    assert result["dirty"] and result["stored"] == []


@pytest.mark.parametrize("navigation", ["tab", "cancel", "hash", "back"])
def test_person_inline_drafts_guard_tab_changes_and_cancellation(navigation: str) -> None:
    result = run_console("person-tabs-guard", data={"navigation": navigation, "mode": "detailed"})
    assert result["prompt"] and result["stayPreserved"] and result["secondPrompt"]
    assert result["discarded"] and result["writes"] == []


@pytest.mark.parametrize("months", [1, 3])
def test_person_extend_menu_previews_a_draft_before_saving(months: int) -> None:
    result = run_console("person-tabs-extend", data={"months": months})
    assert result["menu"] and result["beforeSave"] == []
    assert result["previewReads"] == [f"ui/api/people/sample/expiry?months={months}"]
    assert result["editor"] and result["noScheduleFieldset"] and result["utcLines"] == 1
    assert result["writes"] == [{"expire_at": "2032-06-01T12:00:00Z"}]


@pytest.mark.parametrize("months", [1, 3])
@pytest.mark.parametrize("kind", ["pending", "fixed", "unlimited"])
def test_summary_extension_uses_the_saved_account_mode_even_when_the_editor_differs(months: int, kind: str) -> None:
    result = run_console(
        "person-tabs-extend",
        data={
            "months": months,
            "pending": kind == "pending",
            "beforePreset": "custom" if kind == "pending" else "first_use",
            "user": {"expire_at": None, "schedule": {"expire_at": None}} if kind == "unlimited" else {},
        },
    )
    assert result["error"] == "" and result["editor"]
    if kind == "pending":
        assert result["pendingReview"] and result["expiryMode"] == "first_use"
        assert result["writes"] == [{"activation_duration_days": 30 + months * 30, "expire_at": None}]
    else:
        assert result["expiryMode"] == "fixed" and result["dateEnabled"]
        assert result["writes"] == [{"expire_at": "2032-06-01T12:00:00Z"}]


def test_person_extend_custom_date_is_reviewed_by_the_service_and_refusals_keep_the_draft() -> None:
    result = run_console("person-tabs-extend", data={"custom": True, "failPreview": True})
    assert result["editor"] and result["reviews"] and result["writes"] == []
    assert result["error"] and result["dirty"]


def test_person_refresh_and_mode_switch_preserve_inline_fields_focus_and_menu_state() -> None:
    result = run_console("person-tabs-refresh", data={"mode": "detailed"})
    assert result["sameField"] and result["focusKept"] and result["sameSummary"] and result["menuOpen"]
    assert result["note"] == "Private draft" and result["status"] and result["meter"] == "90"
    assert result["passive"] == [False, True]
    assert result["shownInSimple"] and result["dirty"] and result["stored"] == []


def test_unknown_person_tab_falls_back_to_access_without_a_dead_end() -> None:
    result = run_console("person-tabs-route", data={"tab": "unknown"})
    assert result["selected"] == "access" and result["panels"] == ["access"]


@pytest.mark.parametrize("months", [1, 3])
def test_extending_unused_first_use_access_keeps_activation_pending(
    months: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = "2026-11-02T12:00:00Z"
    service = ControlService(
        tmp_path / "control.db",
        fleet_id=FLEET_ID,
        version="4.0.0",
        clock=lambda: datetime.fromisoformat(now).timestamp(),
    )
    try:
        user = service.create_user(UserCreate(username="sample", activation_duration_days=30))
        preview = service.bulk.preview(
            BulkPreviewRequest(usernames=["sample"], action=BulkAction(action="extend", months=months))
        )
        after = preview.people[0].after
        assert after is not None
        result = run_console(
            "person-tabs-extend",
            data={
                "months": months,
                "pending": True,
                "user": user.to_data(),
                "pendingPreview": preview.to_data(),
            },
        )
        assert result["writes"] == [{"activation_duration_days": after.activation_duration_days, "expire_at": None}]
    finally:
        service.close()
    assert result["pendingReview"] and result["expiryMode"] == "first_use"


@pytest.mark.parametrize("dirty", [False, True])
def test_reducing_mode_from_a_detailed_person_tab_uses_the_navigation_guard(dirty: bool) -> None:
    result = run_console("person-tabs-mode", data={"tab": "devices", "mode": "detailed", "dirty": dirty})
    assert result["prompt"] is dirty
    if dirty:
        assert result["stayed"] and result["secondPrompt"]
    assert result["hash"] == "#/overview" and result["mode"] == "simple"


def test_extend_on_access_does_not_ask_to_discard_a_different_rows_draft() -> None:
    result = run_console("person-tabs-extend", data={"months": 1, "noteDraft": True})
    assert result["prompt"] is False and result["editor"]
    assert result["noteDraft"] == "Private draft" and result["dirty"]


@pytest.mark.parametrize("locale", ["en", "fa"])
def test_inline_time_has_a_full_control_target_and_summary_expiry_is_bidi_isolated(locale: str) -> None:
    result = run_console("person-tabs-appearance", locale)
    assert result["dateTarget"] >= 44
    assert result["timeTarget"] >= 44
    assert result["expiryIsolated"]


def test_expiry_preview_keeps_a_traffic_period_saved_by_another_row() -> None:
    result = run_console("person-tabs-reviews", data={"mode": "detailed"})
    assert result["writes"] == [{"traffic_limit_strategy": "week"}, {"expire_at": "2032-06-01T12:00:00Z"}]
    assert result["expiryPreview"] == {"username": "sample", "expire_at": "2032-06-01T12:00:00Z"}


@pytest.mark.parametrize("duration", [None, 30])
def test_custom_extension_opens_a_fixed_date_draft_for_unlimited_and_pending_access(duration: int | None) -> None:
    result = run_console(
        "person-tabs-extend",
        data={
            "custom": True,
            "user": {
                "expire_at": None,
                "activation_duration_days": duration,
                "schedule": {
                    "expire_at": None,
                    "activation_pending": bool(duration),
                    "activation_duration_days": duration,
                },
            },
        },
    )
    assert result["expiryMode"] == "fixed" and result["dateEnabled"]
    expected = {"expire_at": "2032-06-01T23:59:59Z"}
    if duration:
        expected["activation_duration_days"] = None
    assert result["writes"] == [expected]


@pytest.mark.parametrize("duration", [None, 30])
@pytest.mark.parametrize("existing", [False, True])
def test_custom_date_opens_a_clean_quiet_editor_and_reports_an_empty_save_once(
    duration: int | None, existing: bool
) -> None:
    result = run_console(
        "person-tabs-custom-entry",
        data={
            "existing": existing,
            "submitEmpty": True,
            "user": {"expire_at": None, "activation_duration_days": duration, "schedule": {"expire_at": None}},
        },
    )
    initial = result["initial"]
    assert initial["mode"] == "fixed" and initial["enabled"] and initial["focused"]
    assert initial["invalid"] != "true" and initial["errors"] == [] and not initial["dirty"]
    assert len(result["invalidSave"]["errors"]) == 1 and result["invalidSave"]["focused"]
    assert result["invalidSave"]["writes"] == [] and not result["prompt"] and result["closed"]


def test_custom_date_keeps_a_clean_editor_when_an_earlier_month_preview_arrives_late() -> None:
    result = run_console("person-tabs-custom-race", data={"holdCalendar": True})
    assert result["date"] == "2026-12-02" and result["mode"] == "fixed"
    assert not result["dirty"] and result["writes"] == []


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_traffic_editor_previews_while_editing_and_focuses_then_clears_an_invalid_reset_day(locale: str) -> None:
    result = run_console(
        "person-tabs-traffic-preview",
        locale,
        {"clock": True, "previewFields": PREVIEW_FIELDS, "schedulePreview": {"next_reset_at": "2026-11-15T00:00:00Z"}},
    )
    assert result["initialReset"] == "2026-12-01T00:00:00Z"
    assert len(result["invalid"]["fields"]) == 1 and result["invalid"]["focused"]
    assert (
        result["invalid"]["summary"] == "" and result["invalid"]["previews"] == 0 and result["invalid"]["writes"] == []
    )
    assert result["corrected"] == {"fields": [], "summary": ""}
    editing = result["editing"]
    assert editing["reset"] == "2026-11-15T00:00:00Z" and editing["writes"] == []
    assert [event["body"] for event in editing["previews"]] == [
        {"username": "sample", "traffic_limit_strategy": "month_rolling", "traffic_reset_anchor_day": 15}
    ]
    assert editing["previews"][0]["method"] == "POST"
    assert result["writes"] == [{"traffic_limit_strategy": "month_rolling", "traffic_reset_anchor_day": 15}]
    assert result["closed"]


def test_traffic_quota_edits_preview_only_schedule_fields_and_save_the_new_cap() -> None:
    result = run_console("person-tabs-traffic-quota", data={"clock": True, "previewFields": PREVIEW_FIELDS})
    assert result["editing"]["errors"] == [] and result["editing"]["invalid"] == "false"
    assert result["editing"]["previews"] == [{"username": "sample", "traffic_limit_strategy": "week"}]
    assert result["saved"]["errors"] == [] and result["saved"]["closed"]
    assert result["saved"]["writes"] == [{"traffic_limit_bytes": 200 * 1024**3, "traffic_limit_strategy": "week"}]


def test_a_reset_preview_runs_before_the_operator_types_a_limit() -> None:
    result = run_console(
        "person-tabs-traffic-quota",
        data={"clock": True, "previewFields": PREVIEW_FIELDS, "emptyLimit": True, "user": {"traffic_limit_bytes": 0}},
    )
    assert result["editing"]["previews"] == [{"username": "sample", "traffic_limit_strategy": "week"}]


def test_a_sibling_save_never_moves_focus_into_an_invalid_reset_day() -> None:
    result = run_console("person-tabs-sibling-focus", data={"previewFields": PREVIEW_FIELDS})
    assert result["saved"] and not result["anchorFocused"]


def test_custom_date_over_an_open_first_use_editor_refreshes_its_preview() -> None:
    result = run_console("person-tabs-custom-preview", data={"clock": True, "previewFields": PREVIEW_FIELDS})
    assert result["custom"]["mode"] == "fixed" and result["custom"]["date"] == "2026-12-02"
    assert result["custom"]["previews"] > result["firstUse"]["previews"]
    assert not result["custom"]["last"].get("activation_duration_days")


@pytest.mark.parametrize("first_use", [False, True])
def test_submitting_before_the_preview_debounce_keeps_fields_frozen_through_review_and_patch(first_use: bool) -> None:
    result = run_console(
        "person-tabs-saving-preview",
        data={"clock": True, "holdPreview": True, "holdPatch": True, "firstUse": first_use},
    )
    assert result["startedFrozen"]
    assert result["review"] == {"frozen": True, "saving": True, "previews": 1}
    assert result["patch"] == {"frozen": True, "saving": True, "writes": 1}
    assert result["closed"] and result["sibling"] == {
        "connected": True,
        "value": "Other private draft",
        "enabled": True,
    }


@pytest.mark.parametrize("dirty", [False, True])
def test_sibling_traffic_save_refreshes_an_open_expiry_preview_and_preserves_its_draft(dirty: bool) -> None:
    schedule = {"next_reset_at": "2026-11-09T00:00:00Z"}
    result = run_console(
        "person-tabs-sibling-refresh",
        data={"clock": True, "dirty": dirty, "updatedSchedule": schedule, "schedulePreview": schedule},
    )
    assert result["expiry"]["reset"] == "2026-11-09T00:00:00Z"
    assert result["expiry"]["sameForm"] and result["expiry"]["sameDate"]
    assert result["expiry"]["date"] == ("2032-06-01" if dirty else "2026-12-02")
    assert result["expiry"]["dirty"] is dirty and result["trafficClosed"]
    assert result["writes"] == [{"traffic_limit_strategy": "week"}]


@pytest.mark.parametrize("field", ["note", "expiry"])
def test_passive_refresh_reseeds_a_clean_editor_and_uses_the_current_saved_baseline(field: str) -> None:
    result = run_console(
        "person-tabs-clean-refresh", data={"field": field, "pending": field == "expiry", "user": {"note": "Old"}}
    )
    refreshed = result["refreshed"]
    assert refreshed["value"] == ("New" if field == "note" else "fixed")
    assert not refreshed["dirty"] and refreshed["focused"] and result["passive"]
    if field == "note":
        assert refreshed["selection"] == [1, 2] and result["writes"] == [{"note": "Old"}]
    else:
        assert refreshed["date"] == "2026-12-02"
        assert result["calendarReview"] and not result["pendingReview"]
        assert result["writes"] == [{"expire_at": "2032-06-01T12:00:00Z"}]


def test_passive_polling_keeps_a_draft_without_starting_active_preview_posts() -> None:
    result = run_console("person-tabs-passive-draft", data={"clock": True})
    assert result["before"] == result["after"] == 1
    assert result["passive"] and result["sameField"] and result["dirty"]
    assert result["draft"] == "2032-06-01"
