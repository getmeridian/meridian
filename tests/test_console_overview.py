"""Overview composes real query facets, honest traffic windows, and working handoffs."""

from datetime import datetime
from pathlib import Path

import pytest

from meridian.control.models import UserImport, UserImportBatch
from meridian.control.service import ControlService
from meridian.core.people_query import PeopleQuery
from tests.support.console_dom import needs_console_dom, run_console
from tests.support.control_service import FLEET_ID

pytestmark = needs_console_dom
NOW = "2026-11-02T12:00:00Z"


@pytest.fixture
def overview_data(tmp_path: Path, request: pytest.FixtureRequest) -> dict:
    population = getattr(request, "param", 5)
    service = ControlService(
        tmp_path / "control.db",
        fleet_id=FLEET_ID,
        version="4.0.0",
        clock=lambda: datetime.fromisoformat(NOW).timestamp(),
    )
    try:
        service.import_users(
            UserImportBatch(
                users=[
                    UserImport(
                        username=f"sample-{index}",
                        uuid=f"00000000-0000-4000-8000-{index:012d}",
                        sub_token=f"sample-token-{index:010d}",
                        created_at="2026-10-01T12:00:00Z",
                        expire_at="2026-11-09T12:00:00Z" if index == 0 else None,
                        traffic_limit_bytes=100 if index % 5 in (0, 1, 2) else 0,
                        used_traffic_bytes=[90, 89, 100, 1, 0][index % 5],
                        lifetime_traffic_bytes=[90, 89, 100, 1, 0][index % 5],
                        status="disabled" if index % 5 == 3 else "active",
                    )
                    for index in range(population)
                ]
            )
        )
        pages = {
            key: service.people.query(query).model_dump(mode="json")
            for key, query in {
                "all": PeopleQuery(limit=1),
                "expires": PeopleQuery(expires=7, limit=1),
                "quota": PeopleQuery(quota=90, limit=1),
                "picker": PeopleQuery(limit=20),
            }.items()
        }
        if pages["picker"]["next_cursor"]:
            pages["picker_next"] = service.people.query(
                PeopleQuery(limit=20, cursor=pages["picker"]["next_cursor"])
            ).model_dump(mode="json")
        search = service.people.query(PeopleQuery(limit=20, q="sample-2")).model_dump(mode="json")
        return {"pages": pages, "search_pages": {"sample-2": search}}
    finally:
        service.close()


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_attention_links_match_service_counts_at_the_exact_thresholds(overview_data: dict, locale: str) -> None:
    result = run_console("overview-attention", locale, overview_data)
    assert result["attention"] == {
        "#/people?expires=7": "1",
        "#/people?quota=90": "2",
        "#/people?status=limited": "1",
        "#/people?status=disabled": "1",
    }
    assert result["people"] == {"active": "3", "total": "5"}
    assert all(
        "limit=1" in request["url"] for request in result["requests"] if request["url"].startswith("api/people?")
    )
    assert result["writes"] == []


def test_never_connected_is_a_detailed_only_attention_row(overview_data: dict) -> None:
    result = run_console("overview-mode", data=overview_data)
    assert "#/people?never=1" not in result["beforeAttention"]
    assert result["attention"]["#/people?never=1"] == "1"
    assert result["mode"] == "detailed"


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_empty_overview_has_a_calm_check_and_keeps_add_and_share_available(locale: str) -> None:
    result = run_console("overview-empty", locale, {"fleet": {"profile_title": "Sample VPN", "servers": []}})
    assert result["calmEmpty"] and result["emptyCheck"] and result["add"] and result["share"]
    assert result["attention"] == {} and result["emptyCatalogText"] in result["text"]


def test_server_attention_links_open_the_server_and_payment_reminders() -> None:
    result = run_console(
        "overview-attention",
        data={
            "fleet": {
                "profile_title": "Sample VPN",
                "servers": [
                    {"node_id": "offline", "live": False, "runtime_available": False, "in_sync": False},
                    {"node_id": "behind", "live": True, "runtime_available": True, "in_sync": False},
                ],
                "server_attention": [{"server_ref": "relay", "reasons": ["payment_due"]}],
                "server_usage": [{"server_ref": "relay", "node_id": None, "next_payment_on": "2026-11-08"}],
            }
        },
    )
    assert set(result["attention"]) == {"#/servers/offline", "#/servers/behind", "#/servers/relay"}
    assert "2" in result["fleetHealth"] and result["healthKind"] == "warn"


def test_traffic_uses_the_returned_window_and_never_node_lifetime_bytes() -> None:
    result = run_console(
        "overview-attention",
        data={"daily": {"days": 7, "history_days": 7, "daily": [{"day": "2026-11-02", "bytes": 4096}], "nodes": []}},
    )
    assert result["traffic"] == "4 KiB" and result["trafficDays"] == "7"


@pytest.mark.parametrize("setting", ["historyOff", "historyError"])
def test_unavailable_history_never_claims_zero_counted_traffic(setting: str) -> None:
    result = run_console("overview-attention", data={setting: True})
    assert result["traffic"] == "—" and result["historyUnavailable"]


def test_detailed_recent_changes_merge_newest_revisions_and_owner_edits() -> None:
    result = run_console(
        "overview-attention",
        data={
            "storage": {"mrd.mode": "detailed"},
            "daily": {"days": 1, "history_days": 7, "daily": [{"day": "2026-11-02", "bytes": 4096}], "nodes": []},
            "events": [
                {
                    "id": 1,
                    "time": "2026-11-02T09:00:00Z",
                    "kind": "user.updated",
                    "actor": "owner",
                    "subject": "sample-0",
                },
                {
                    "id": 2,
                    "time": "2026-11-02T11:00:00Z",
                    "kind": "notice.changed",
                    "actor": "owner",
                    "subject": "notice",
                },
                {"id": 3, "time": "2026-11-02T11:30:00Z", "kind": "node.offline", "actor": "system", "subject": "main"},
            ],
            "revisions": [
                {"number": 2, "status": "applied", "created_at": "2026-11-02T10:00:00Z", "author": {"name": "root"}}
            ],
        },
    )
    assert result["recent"] == ["notice.changed", "revision:2", "user.updated"]
    assert result["sparkline"]


def test_overview_refresh_is_passive_visible_and_stops_on_navigation(overview_data: dict) -> None:
    result = run_console("overview-poll", data=overview_data)
    assert result["visibleRefresh"] > result["initialReads"]
    assert result["hiddenReads"] == result["visibleRefresh"]
    assert result["disposedReads"] == result["resumedReads"]
    assert result["passiveRefresh"] and result["keptDataOnFailure"]
    assert result["stale"] and result["retry"]


def test_share_picker_opens_the_existing_local_qr_view_without_changing_data(overview_data: dict) -> None:
    result = run_console("overview-share", data=overview_data)
    assert result["pickerPeople"] == [f"sample-{index}" for index in range(5)]
    assert result["shareImage"] and result["shareCopy"] and result["qrFormats"] == ["PNG", "SVG"]
    assert result["writes"] == []
    assert sum(request["url"].endswith("/share") for request in result["requests"]) == 1


def test_add_action_uses_the_existing_person_form() -> None:
    result = run_console("overview-add")
    assert result["addForm"] and result["writes"] == []


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
@pytest.mark.parametrize("width", [390, 1440])
def test_quick_actions_use_the_desktop_header_and_reserve_a_solid_phone_bar(locale: str, width: int) -> None:
    result = run_console(
        "overview-layout",
        locale,
        {
            "width": width,
            "storage": {"mrd.mode": "detailed"},
            "daily": {
                "days": 2,
                "history_days": 7,
                "daily": [{"day": "2026-11-01", "bytes": 1024}, {"day": "2026-11-02", "bytes": 4096}],
                "nodes": [],
            },
        },
    )
    layout = result["layout"]
    assert layout["singleActionGroup"] and layout["primary"] and layout["headerActions"]
    assert layout["dates"] == ["2026-11-01", "2026-11-02"] and "4 KiB" in layout["peak"]
    if width > 640:
        assert layout["position"] == "static" and layout["headerDisplay"] == "flex"
    else:
        assert layout["position"] == "sticky" and layout["headerDisplay"] == "contents"
        assert layout["order"] == "1"
        assert "var(--s1)" in layout["surface"] and "solid" in layout["border"] and layout["shadow"]
        assert "safe-area-inset-bottom" in layout["safePadding"]
        assert "safe-area-inset-bottom" in layout["pagePadding"] and "44px" in layout["pagePadding"]


def test_an_initial_read_failure_keeps_an_honest_state_and_retry_recovers() -> None:
    result = run_console("overview-recover", data={"initialFailure": True})
    assert result["noFalseEmpty"] and result["recovered"]


@pytest.mark.parametrize("overview_data", [50], indirect=True)
@pytest.mark.parametrize("locale", ["en", "fa"])
def test_share_picker_search_and_paging_keep_the_input_and_focus(overview_data: dict, locale: str) -> None:
    result = run_console("overview-picker", locale, overview_data)
    assert result["pickerNext"] == [person["username"] for person in overview_data["pages"]["picker_next"]["users"]]
    assert result["pickerSearch"] == [
        person["username"] for person in overview_data["search_pages"]["sample-2"]["users"]
    ]
    assert result["sameSearch"] and result["searchFocus"]
    assert all(
        "limit=20" in request["url"]
        for request in result["requests"]
        if request["url"].startswith("api/people?") and "limit=1" not in request["url"]
    )


@pytest.mark.parametrize("overview_data", [50], indirect=True)
def test_share_picker_ignores_a_page_reply_after_the_search_changes(overview_data: dict) -> None:
    result = run_console("overview-picker-race", data={**overview_data, "pickerRace": True})
    assert result["ignoredOldPage"] and result["sameSearch"] and result["searchFocus"]


@pytest.mark.parametrize("overview_data", [50], indirect=True)
def test_share_picker_recovers_an_expired_cursor_with_the_first_page(overview_data: dict) -> None:
    result = run_console("overview-picker-expired", data={**overview_data, "expiredPicker": True})
    assert result["cursorRecovered"]
    assert result["pickerNext"] == [person["username"] for person in overview_data["pages"]["picker"]["users"]]


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
@pytest.mark.parametrize(("theme", "background"), [("light", "#FDFAF6"), ("dark", "#1C1E28")])
def test_phone_theme_and_rtl_contracts_cover_the_shared_components(
    overview_data: dict, locale: str, theme: str, background: str
) -> None:
    result = run_console("overview-visual", locale, {**overview_data, "storage": {"mrd.theme": theme}})
    visual = result["visual"]
    assert visual["background"] == background
    assert visual["columns"] == "minmax(0, 1fr)"
    assert visual["readAlignment"] == "end" and visual["menuEdge"] == "0px"
    assert all(height >= 44 for height in visual["targets"])
    assert result["dir"] == ("rtl" if locale == "fa" else "ltr")
    assert visual["ageIsolated"] and visual["meter"]


def test_partial_history_failures_keep_evidence_with_backoff_and_history_off_clears_it() -> None:
    result = run_console(
        "overview-history-poll",
        data={"daily": {"days": 7, "history_days": 7, "daily": [{"day": "2026-11-02", "bytes": 4096}], "nodes": []}},
    )
    assert result["beforeTraffic"] == result["staleTraffic"] == "4 KiB"
    assert result["historyStale"] and result["partialRetry"] and result["historyBackoff"]
    assert result["offTraffic"] == "—" and result["offUnavailable"]
