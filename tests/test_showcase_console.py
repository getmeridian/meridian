"""Exercise the exported console against its demo adapter, without any service running."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from scripts.build_showcase import export_showcase, validate_recordings
from tests.support.console_dom import needs_console_dom, run_console

pytestmark = needs_console_dom


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("console-demo")
    export_showcase(directory)
    return directory


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_demo_opens_people_and_navigates_the_real_screens_without_api_requests(demo: Path, locale: str) -> None:
    result = run_console("showcase-navigation", locale, {"directory": str(demo)})
    assert result["lang"] == locale
    assert result["dir"] == ("rtl" if locale == "fa" else "ltr")
    assert result["people"] == 6
    assert result["rendered_people"] == 6
    assert result["person"] == "Luma"
    assert result["connections"] == 8
    assert result["qr"]
    assert result["servers"] == 2
    assert result["settings"] and result["tokens"] == 3
    assert result["banner"] == f"/demo/?lang={locale}"
    assert result["outside"] == result["nativeApi"] == result["errors"] == []


def test_demo_mutations_preserve_current_contracts_and_reset_with_a_new_document(demo: Path) -> None:
    result = run_console("showcase-mutations", data={"directory": str(demo)})
    validate_recordings(result["responses"])
    assert result["edited"] == "Literal <b>demo note</b>"
    assert result["devices"] == 0
    assert result["notice"] == "Hello Luma"
    assert result["language"] == "zh"
    assert result["reissued"] and result["token_revoked"] and result["removed"]
    assert result["reissuedGenerationMatches"]
    assert result["guestReissued"], "a person added in the demo can be reissued twice"
    assert result["velaLinks"] > 0, "enabling a disabled person shows working links, not placeholders"
    assert result["week"] == 7 and result["serverDays"] == 7
    assert result["historyRestarted"], "history turned off and on again starts empty instead of failing"
    assert "user.enabled" in result["events"] and "user.updated" in result["events"]
    assert result["outside"] == result["nativeApi"] == result["errors"] == []
    reset = run_console("showcase-reset", data={"directory": str(demo)})
    assert reset["note"] != result["edited"]
    assert reset["language"] == "auto" and reset["devices"] == 2


def test_a_language_switch_moves_into_the_demo_address_so_reloads_and_shared_links_keep_it(demo: Path) -> None:
    """Demo storage lives in memory, so `?lang=` is the only thing a reload or a shared link carries."""
    result = run_console("showcase-language", "en", {"directory": str(demo)})
    assert result["search"] == "?lang=ru"
    assert result["hash"] and result["state"]
    assert result["banner"] == "/demo/?lang=ru"
    assert result["errors"] == []
def test_demo_people_queries_match_service_recordings_and_keep_edits_across_pages(demo: Path) -> None:
    result = run_console("showcase-queries", data={"directory": str(demo)})
    validate_recordings(result["responses"])
    assert result["serviceQueries"] >= 25
    assert result["queryMismatches"] == []
    assert result["paged"] == result["ordered"]
    assert result["cursorRejectsChangedQuery"] and result["cursorExcludesNewPeople"]
    assert result["editedSearch"] == ["Luma"]
    assert result["editedTag"] == 1 and result["privateFields"] == []
    assert result["outside"] == result["nativeApi"] == result["errors"] == []


def test_demo_bulk_and_routing_use_reviews_receipts_and_effective_access(demo: Path) -> None:
    result = run_console("showcase-features", data={"directory": str(demo)})
    validate_recordings(result["responses"])
    assert result["previewDoesNotWrite"] and result["commitIdempotent"] and result["undoRestores"]
    assert result["staleRejected"] and result["expiryReviewed"]
    assert result["profiles"] and result["accessChanged"] and result["shareChanged"]
    assert result["policyPreviewDoesNotWrite"] and result["policyConfirmed"] and result["stalePolicyRejected"]
    assert result["formats"] == ["base64", "browser", "mihomo", "singbox", "stash", "xray-json"]
    assert result["customRule"] and result["blockedRule"]
    assert result["passkeys"] == []
    assert result["rotated"] and result["rotationDevicesIntact"] and result["staleKeysRejected"]
    assert result["periodReset"] and result["unlimitedDevices"]
    assert result["declaredKept"] and result["bulkDeleted"] and result["deleteUndoRefused"]
    assert result["outside"] == result["nativeApi"] == result["errors"] == []


@pytest.mark.parametrize(
    "create,strategy", [(True, "month"), (True, "day"), (True, "week"), (False, "day"), (False, "week")]
)
def test_demo_stores_due_period_resets_before_a_later_bulk_commit(demo: Path, create: bool, strategy: str) -> None:
    result = run_console(
        "showcase-schedule-catchup", data={"directory": str(demo), "create": create, "strategy": strategy}
    )
    assert result["stored"]["traffic_reset_at"] is not None
    assert result["stored"]["used_traffic_bytes"] == 0
    assert result["review"]["before"]["traffic_reset_at"] == result["stored"]["traffic_reset_at"]
    assert result.get("commitError") is None
    assert result["committed"]["after"]["tag"] == result["after"]["tag"] == "VIP"
    validate_recordings(result["responses"])
    assert result["outside"] == result["nativeApi"] == result["errors"] == []


def test_demo_materializes_first_use_expiry_before_status_and_lifecycle_queries(demo: Path) -> None:
    result = run_console("showcase-schedule-first-use", data={"directory": str(demo)})
    expiry = datetime.fromisoformat(result["before"]["first_traffic_at"]) + timedelta(days=5)
    expected = expiry.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert result["previewKeptUser"]
    assert (
        result["preview"]["expire_at"]
        == result["after"]["expire_at"]
        == result["after"]["schedule"]["expire_at"]
        == expected
    )
    assert result["after"]["status"] == "expired"
    assert result["fixed"] == ["Luma"] and result["unlimited"] == []
    validate_recordings(result["responses"])
    assert result["outside"] == result["nativeApi"] == result["errors"] == []


@pytest.mark.parametrize(
    "person,action,expired",
    [
        ("Kavi", {"action": "disable"}, False),
        ("Luma", {"action": "disable"}, True),
        ("Kavi", {"action": "quota", "traffic_limit_bytes": 100 * 1024**3}, False),
        ("Luma", {"action": "quota", "traffic_limit_bytes": 1024**3}, False),
        ("Luma", {"action": "expiry"}, False),
        ("Luma", {"action": "extend", "days": 7}, True),
    ],
)
def test_demo_undo_restores_the_reviewed_enforced_status(demo: Path, person: str, action: dict, expired: bool) -> None:
    result = run_console(
        "showcase-undo-status", data={"directory": str(demo), "person": person, "action": action, "expired": expired}
    )
    assert result["committed"]["outcome"] == "changed"
    assert result["committed"]["before"]["status"] != result["committed"]["after"]["status"]
    assert result.get("undoError") is None
    assert result["dryDidNotWrite"]
    assert result["dry"][0]["after"] == result["undone"][0]["after"] == result["committed"]["before"]
    assert result["after"]["status"] == result["before"]["status"]
    validate_recordings(result["responses"])
    assert result["outside"] == result["nativeApi"] == result["errors"] == []


def test_demo_undo_ignores_people_unchanged_by_the_commit(demo: Path) -> None:
    result = run_console("showcase-undo-unchanged", data={"directory": str(demo)})
    assert result["outcomes"] == ["changed", "unchanged"]
    assert result.get("undoError") is None
    assert result["undone"] == ["Luma"] and result["restored"]
    assert result["untouched"] == "AFTER"
    validate_recordings(result["responses"])
    assert result["outside"] == result["nativeApi"] == result["errors"] == []


@pytest.mark.parametrize("person,reason", [("Vela", "disabled"), ("Kavi", "limited")])
@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_demo_keeps_recorded_inactive_share_receipts_and_placeholders(
    demo: Path, person: str, reason: str, locale: str
) -> None:
    result = run_console("showcase-share-initial", locale, {"directory": str(demo), "person": person})
    assert result["recorded"]["flow_blocks"]["nodes"]
    assert result["share"]["flow_blocks"] == result["recorded"]["flow_blocks"]
    assert all(node["reason"] == reason for node in result["share"]["flow_blocks"]["nodes"])
    assert result["share"]["connections"] == result["recorded"]["connections"]
    assert result["card"]
    validate_recordings(result["responses"])
    assert result["outside"] == result["nativeApi"] == result["errors"] == []


@pytest.mark.parametrize("person", ["Luma", "DemoGuest", "constructor", "toString"])
@pytest.mark.parametrize("action", ["rotate-credentials", "reissue"])
def test_demo_repeated_key_changes_keep_receipts_for_the_newly_revoked_generation(
    demo: Path, person: str, action: str
) -> None:
    result = run_console("showcase-share-rotations", data={"directory": str(demo), "person": person, "action": action})
    for change in result["changes"]:
        user = change["result"]["user"] if action == "rotate-credentials" else change["result"]
        receipts = change["share"]["flow_blocks"]
        assert receipts["credential_generation"] == user["credential_generation"]
        assert {node["credential_generation"] for node in receipts["nodes"]} == {
            change["before"]["credential_generation"]
        }
        assert all(node["reason"] == "credentials" for node in receipts["nodes"])
        if action == "rotate-credentials":
            assert change["result"]["flow_blocks"] == receipts
        validate_recordings(
            {
                f"GET ui/api/people/{person}/share": change["share"],
                f"POST api/users/{person}/{action}": change["result"],
            }
        )
    assert result["card"]
    assert result["outside"] == result["nativeApi"] == result["errors"] == []


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
def test_demo_security_and_remote_actions_show_a_translated_demo_refusal(demo: Path, locale: str) -> None:
    result = run_console("showcase-unavailable", locale, {"directory": str(demo)})
    assert len(result["refusals"]) >= 10
    assert all(reply["code"] == "demo_unavailable" for reply in result["refusals"])
    assert all(reply["text"] and reply["text"] != result["missing"] for reply in result["refusals"])
    assert len({reply["text"] for reply in result["refusals"]}) == 1
    assert result["outside"] == result["nativeApi"] == result["errors"] == []
