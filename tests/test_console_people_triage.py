"""People filters and sorting persist in the fragment without rebuilding the list."""

from urllib.parse import parse_qs

import pytest

from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


def people() -> list[dict]:
    return [
        {
            "username": "p-a",
            "status": "active",
            "tag": "GROUP_A",
            "expire_at": "2026-11-03T12:00:00Z",
            "used_traffic_bytes": 80,
            "traffic_limit_bytes": 100,
            "quota_used_percent": 80,
            "online_at": "2026-10-28T12:00:00Z",
            "never_used": False,
            "attention_reasons": ["expiring", "quota", "stopped"],
        },
        {
            "username": "p-b",
            "status": "disabled",
            "tag": "GROUP_B",
            "expire_at": None,
            "used_traffic_bytes": 0,
            "traffic_limit_bytes": 0,
            "quota_used_percent": None,
            "online_at": None,
            "never_used": True,
            "attention_reasons": ["unused"],
        },
        {
            "username": "p-c",
            "status": "active",
            "tag": "GROUP_A",
            "expire_at": "2026-12-01T12:00:00Z",
            "used_traffic_bytes": 79,
            "traffic_limit_bytes": 100,
            "quota_used_percent": 79,
            "online_at": "2026-11-01T11:00:00Z",
            "never_used": False,
            "attention_reasons": [],
        },
        {
            "username": "p-d",
            "status": "expired",
            "tag": "",
            "expire_at": "2026-10-31T12:00:00Z",
            "used_traffic_bytes": 30,
            "traffic_limit_bytes": 100,
            "quota_used_percent": 30,
            "online_at": "2026-10-29T12:00:00Z",
            "never_used": False,
            "attention_reasons": [],
        },
    ]


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"status": "active"}, ["p-a", "p-c"]),
        ({"tag": "GROUP_B"}, ["p-b"]),
        ({"expires": 7}, ["p-a"]),
        ({"quota": 80}, ["p-a"]),
        ({"never": True}, ["p-b"]),
        ({"attention": "stopped"}, ["p-a"]),
        ({"attention": "unused"}, ["p-b"]),
        ({"status": "disabled", "tag": "GROUP_A"}, []),
    ],
)
def test_each_triage_filter_and_intersections_keep_their_state(change: dict, expected: list[str]) -> None:
    result = run_console("people-triage-reload", data={"people": people(), "change": change})
    assert result["shown"] == expected and result["afterReload"] == expected
    assert result["stored"] == []
    if expected == []:
        assert "No people match these filters" in result["text"]


@pytest.mark.parametrize(
    ("sort", "order", "expected"),
    [
        ("name", "asc", ["p-a", "p-b", "p-c", "p-d"]),
        ("usage", "desc", ["p-a", "p-c", "p-d", "p-b"]),
        ("expiry", "asc", ["p-d", "p-a", "p-c", "p-b"]),
        ("activity", "desc", ["p-c", "p-d", "p-a", "p-b"]),
    ],
)
def test_sorting_uses_existing_evidence_and_places_unknown_dates_last(
    sort: str, order: str, expected: list[str]
) -> None:
    result = run_console("people-triage", data={"people": people(), "hash": f"#/people?sort={sort}&order={order}"})
    assert result["shown"] == expected
    assert result["route"]["name"] == "people" and result["route"]["arg"] == ""


def test_facet_counts_respect_the_other_filters() -> None:
    result = run_console("people-triage", data={"people": people(), "change": {"status": "active"}})
    assert "(2)" in next(option["text"] for option in result["selects"]["tag"] if option["value"] == "GROUP_A")
    assert "(0)" in next(option["text"] for option in result["selects"]["tag"] if option["value"] == "GROUP_B")
    assert "(1)" in next(option["text"] for option in result["selects"]["attention"] if option["value"] == "stopped")


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_search_by_display_name_preserves_rows_focus_and_fragment(locale: str) -> None:
    rows = people()
    rows += [{**rows[2], "username": f"p-extra-{index:04d}"} for index in range(3000 if locale == "en" else 50)]
    result = run_console(
        "people-triage-reload",
        locale,
        {"people": rows, "names": {"p-b": "Sample label"}, "change": {"q": "Sample label"}},
    )
    assert result["shown"] == result["afterReload"] == ["p-b"]
    assert result["sameRows"] and result["sameInput"] and result["focusKept"]
    assert parse_qs(result["hash"].split("?", 1)[1])["q"] == ["Sample label"]
    assert result["afterSearch"] == "Sample label"
    assert result["requests"].count("api/users") == 2


def test_bad_fragment_options_are_ignored_and_invalid_edits_have_a_specific_error() -> None:
    result = run_console(
        "people-triage",
        data={
            "people": people(),
            "hash": "#/people?status=unknown&expires=bad&quota=101&sort=bad",
            "change": {"expires": "1.5"},
        },
    )
    assert result["shown"] == ["p-a", "p-b", "p-c", "p-d"]
    assert any("whole number" in error for error in result["errors"])


def test_quota_filter_uses_the_exact_service_share_instead_of_rounded_javascript_numbers() -> None:
    row = {
        **people()[0],
        "traffic_limit_bytes": 9007199254740997,
        "used_traffic_bytes": 7205759403792797,
        "quota_used_percent": 79,
        "attention_reasons": [],
    }
    result = run_console("people-triage", data={"people": [row], "change": {"quota": 80}})
    assert result["shown"] == []
