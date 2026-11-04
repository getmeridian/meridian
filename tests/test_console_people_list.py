"""People keeps common actions visible and selection out of the fresh Simple view."""

import json
import re
from typing import get_args
from urllib.parse import parse_qs

import pytest

from meridian.control.ui_routes import UI_DIR
from meridian.core.people_bulk import BulkActionName
from tests.support.console_dom import needs_console_dom, run_console
from tests.test_console_people_triage import people

pytestmark = needs_console_dom


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_people_selection_shows_a_sticky_bar_only_when_needed_and_matching_only_after_the_page(locale):
    rows = people()
    rows += [{**rows[2], "username": f"p-extra-{index:03d}"} for index in range(100)]
    result = run_console("people-triage-ux", locale, {"people": rows, "scenario": "selection"})
    assert result["initial"]["barHidden"] and result["initial"]["matchingHidden"]
    assert result["initial"]["checkboxes"] == 0 and not result["initial"]["pageSelectVisible"]
    assert result["initial"]["toggleVisible"] and result["addVisible"]
    assert result["selecting"]["checkboxes"] == 100 and result["selecting"]["pageSelectVisible"]
    assert result["one"]["selected"] == 1 and not result["one"]["barHidden"]
    assert result["one"]["matchingHidden"] and result["one"]["reserved"]
    assert result["direct"] == ["extend", "disable"]
    assert result["moreOpened"] and result["moreEscaped"]
    assert set(result["more"]) == set(get_args(BulkActionName)) - set(result["direct"])
    assert result["page"]["selected"] == 100 and not result["page"]["matchingHidden"]
    assert result["detailed"]["checkboxes"] == 100 and not result["detailed"]["toggleVisible"]
    assert result["simpleAgain"]["checkboxes"] == 100 and result["simpleAgain"]["togglePressed"] == "true"
    assert result["refreshed"] == result["simpleAgain"] and result["sameRows"]
    assert result["matching"]["selected"] == 104 and result["matching"]["matchingHidden"]
    assert "\u2068" in result["countText"] and "\u2069" in result["countText"]
    assert result["cleared"]["selected"] == 0 and result["cleared"]["barHidden"] and not result["cleared"]["reserved"]
    assert result["finished"]["checkboxes"] == 0 and not result["finished"]["pageSelectVisible"]
    assert result["stored"] == ["mrd.mode"]
    assert any(request["passive"] for request in result["requests"])


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_detailed_people_exposes_selection_without_an_empty_bulk_toolbar(locale):
    result = run_console("people-triage-ux", locale, {"people": people(), "displayMode": "detailed"})
    assert result["initial"]["checkboxes"] == 4 and result["initial"]["pageSelectVisible"]
    assert result["initial"]["barHidden"] and not result["initial"]["toggleVisible"]
    assert result["addVisible"]


@pytest.mark.parametrize("locale", ["en", "fa"])
def test_desktop_selection_keeps_the_count_actions_and_matching_offer_in_one_compact_row(locale):
    rows = people() + [{**people()[2], "username": f"p-extra-{index:03d}"} for index in range(100)]
    result = run_console(
        "people-triage-ux",
        locale,
        {"people": rows, "displayMode": "detailed", "scenario": "bar-layout", "width": 1040},
    )
    layout = result["layout"]
    assert layout["visible"] and layout["matchingVisible"] and layout["position"] == "sticky"
    assert layout["direction"] == "row" and layout["wrap"] == "nowrap"
    assert layout["countGrows"] == "0" and layout["matchingBasis"] != "100%"


def test_phone_selection_keeps_its_count_above_four_actions():
    rows = people() + [{**people()[2], "username": f"p-extra-{index:03d}"} for index in range(100)]
    result = run_console(
        "people-triage-ux",
        data={"people": rows, "displayMode": "detailed", "scenario": "bar-layout", "width": 390},
    )
    assert result["layout"]["position"] == "fixed"
    assert result["layout"]["display"] == "grid" and result["layout"]["actionsDisplay"] == "flex"
    assert result["layout"]["matchingVisible"] and result["layout"]["matchingMargin"] == "0px"
    assert result["layout"]["matchingSize"] == "100%"


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("status", "active"),
        ("tag", "GROUP_A"),
        ("expires", "7"),
        ("quota", "80"),
        ("never", "1"),
        ("attention", "stopped"),
        ("lifecycle", "fixed"),
        ("profile", "default"),
        ("q", "p-a"),
        ("sort", "usage"),
    ],
)
def test_filter_chips_remove_one_filter_clear_selection_and_survive_reload(locale, key, value):
    result = run_console(
        "people-triage-ux",
        locale,
        {"people": people(), "hash": f"#/people?{key}={value}", "scenario": "chips", "remove": key},
    )
    assert set(result["filterChips"]) == {
        "status",
        "expires",
        "quota",
        "tag",
        "profile",
        "never",
        "attention",
        "lifecycle",
        "sort",
    }
    assert result["removeVisible"] and [chip["key"] for chip in result["active"]] == [key]
    assert result["active"][0]["label"] and key not in parse_qs(result["afterHash"].partition("?")[2])
    assert result["afterRemove"] == result["reloaded"] == []
    assert result["afterSelected"] == 0
    assert result["shown"] == ["p-a", "p-b", "p-c", "p-d"]
    if key != "q":
        assert result["focusAfterRemove"] == key
        assert result["panelOpened"] and result["panelKept"] and result["panelEscaped"]
    assert result["dir"] == ("rtl" if locale == "fa" else "ltr")
    assert result["stored"] == []


def test_removing_one_chip_keeps_the_other_filter():
    result = run_console(
        "people-triage-ux",
        data={"people": people(), "hash": "#/people?status=active&tag=GROUP_A", "scenario": "chips", "remove": "tag"},
    )
    assert [chip["key"] for chip in result["afterRemove"]] == ["status"]
    assert result["controls"]["status"] == "active" and result["controls"]["tag"] == ""
    assert result["shown"] == ["p-a", "p-c"]


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_phone_filter_controls_scroll_in_one_row_and_active_chips_stay_removable(locale):
    result = run_console(
        "people-triage-ux",
        locale,
        {
            "people": people(),
            "scenario": "chips",
            "width": 390,
            "hash": "#/people?status=active&tag=GROUP_A",
            "remove": "tag",
        },
    )
    layout = result["filterLayout"]
    assert layout["wrap"] == "nowrap" and layout["overflow"] == "auto"
    assert all(value == "0" for value in layout["shrink"])
    assert layout["underSearch"] and layout["activeOutside"] and layout["panelPosition"] == "fixed"
    assert result["removeVisible"] and [chip["key"] for chip in result["afterRemove"]] == ["status"]
    assert result["afterSelected"] == 0 and result["panelKept"] and result["panelEscaped"]


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_people_rows_show_compact_usage_and_secondary_facts_and_activate_as_links(locale):
    rows = people()
    rows[0].update(used_traffic_bytes=51 * 1024**3, traffic_limit_bytes=100 * 1024**3, note="Private sample note")
    result = run_console("people-triage-ux", locale, {"people": rows, "scenario": "row"})
    row = result["row"]
    assert row["tag"] == "GROUP_A" and row["meter"] == 51
    assert row["expiry"] and row["exactExpiry"] and row["secondary"]
    assert row["notes"] and not row["nestedCheckbox"]
    assert "Private sample note" not in result["text"]
    assert result["clicked"]["name"] == result["keyboard"]["name"] == "people"
    assert result["clicked"]["arg"] == result["keyboard"]["arg"] == "p-a"
    if locale == "en":
        assert re.sub("[\u2066-\u2069]", "", row["caption"]) == "51 of 100 GiB"


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
@pytest.mark.parametrize("percent", [90, 100, 510])
def test_quota_attention_reads_near_only_before_the_limit_is_reached(locale, percent):
    rows = people()
    rows[0].update(used_traffic_bytes=percent * 1024**3, traffic_limit_bytes=100 * 1024**3)
    result = run_console("people-triage-ux", locale, {"people": rows, "scenario": "row"})
    label = json.loads((UI_DIR / "locales" / f"{locale}.json").read_text())["attention.reason.quota"]
    assert (label in result["row"]["reasons"]) is (percent < 100)


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_empty_people_offers_add_and_no_matches_offers_clear_filters(locale):
    empty = run_console("people-triage-ux", locale, {"people": [], "scenario": "empty"})
    assert empty["empty"]["visible"] and empty["empty"]["role"] == "status"
    assert empty["empty"]["add"] and not empty["empty"]["clear"] and empty["additions"] == 1
    filtered = run_console(
        "people-triage-ux",
        locale,
        {"people": people(), "hash": "#/people?status=disabled&tag=GROUP_A", "scenario": "empty", "clear": True},
    )
    assert filtered["empty"]["visible"] and filtered["empty"]["clear"] and not filtered["empty"]["add"]
    assert filtered["afterEmpty"] == ["p-a", "p-b", "p-c", "p-d"] and filtered["afterHash"] == "#/people"


def test_phone_people_bar_has_a_solid_safe_surface_and_reserves_room_for_its_matching_action():
    css = (UI_DIR / "people.css").read_text()
    phone = css[css.index("@media (max-width: 640px)") :]
    assert re.search(r"\.people-bulk-bar\s*\{[^}]*position:\s*fixed", phone)
    assert "env(safe-area-inset-bottom)" in phone and "background: var(--s1)" in phone
    assert re.search(r"\.people-page\.has-selection\s*\{[^}]*padding-block-end:", phone)
    assert re.search(r"\.people-page\.has-matching-offer\s*\{[^}]*padding-block-end:", phone)
    assert re.search(r"\.people-item\s*\{[^}]*border-radius:\s*var\(--r2\)", phone)


def test_persian_quota_chip_isolates_the_percentage_as_one_value():
    result = run_console("people-triage-ux", "fa", {"people": people(), "hash": "#/people?quota=80"})
    assert re.search("\u2068[^\u2069]*٪\u2069", result["active"][0]["text"])
