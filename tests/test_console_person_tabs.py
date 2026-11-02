"""Person summaries, fragment tabs, and inline drafts use the shipped console."""

from datetime import datetime
from pathlib import Path

import pytest

from meridian.control.models import UserCreate
from meridian.control.service import ControlService
from meridian.core.people_bulk import BulkAction, BulkPreviewRequest
from tests.support.console_dom import needs_console_dom, run_console
from tests.support.control_service import FLEET_ID

pytestmark = needs_console_dom


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


@pytest.mark.parametrize("tab", ["", "usage", "devices", "routing", "keys"])
def test_person_tab_deep_links_select_one_labelled_panel(tab: str) -> None:
    result = run_console("person-tabs-route", data={"tab": tab, "mode": "detailed"})
    assert result["tabs"] == ["access", "usage", "devices", "routing", "keys"]
    assert result["selected"] == (tab or "access")
    assert result["panels"] == [tab or "access"] and result["labelled"]
    assert result["hash"] == "#/people/sample" + ("/" + tab if tab else "")
    assert result["content"]


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_simple_access_is_a_direct_panel_and_detailed_restores_aria_tabs(locale: str) -> None:
    result = run_console("person-tabs-single", locale)
    assert result["before"]["stripHidden"] and result["before"]["tabs"] == 0
    assert result["before"]["role"] == "region" and result["before"]["label"] and result["before"]["content"]
    assert not result["after"]["stripHidden"] and result["after"]["tabs"] == 5
    assert result["after"]["role"] == "tabpanel" and result["after"]["labelled"] and result["after"]["samePanel"]


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
    monkeypatch.setattr("meridian.control.service.utc_now", lambda: now)
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
