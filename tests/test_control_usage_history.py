"""Daily usage counts only the growth already accepted by the cumulative ledger."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from meridian.control.client import ControlClient, ControlServiceHistoryOff
from meridian.control.errors import ServiceError
from meridian.control.models import PrivacySettings
from meridian.control.privacy import history_cutoff
from meridian.control.service import ControlService
from meridian.core.agent_protocol import StatsReport
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID, serve_control

EPOCH_A = "5b0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00"
EPOCH_B = "6c1d8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00"
BEFORE_MIDNIGHT = "2026-10-28T23:59:59Z"
AFTER_MIDNIGHT = "2026-10-29T00:00:01Z"


@pytest.fixture
def service(tmp_path: Path) -> Iterator[ControlService]:
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    service.put_user("alice")
    service.put_user("bob")
    yield service
    service.close()


def _report(service: ControlService, seq: int, epoch: str = EPOCH_A, **totals: tuple[int, int]) -> StatsReport:
    return StatsReport.model_validate(
        {
            "type": "stats",
            "counter_epoch": epoch,
            "seq": seq,
            "collected_at": BEFORE_MIDNIGHT,
            "users": [
                {"user_id": service.get_user(name).user_id, "uplink_bytes": up, "downlink_bytes": down}
                for name, (up, down) in totals.items()
            ],
        }
    )


def _daily(service: ControlService, name: str = "alice") -> list[tuple[date, int, int]]:
    usage = service.traffic.user_usage(name, days=2, now=AFTER_MIDNIGHT)
    return [(row.day, row.uplink_bytes, row.downlink_bytes) for row in usage.daily]


def test_off_records_no_history_and_enabling_never_backfills_old_growth_or_a_replayed_report(
    service: ControlService,
) -> None:
    first = _report(service, 1, alice=(100, 200))
    service.record_stats("main", first, BEFORE_MIDNIGHT)
    assert service._store.rows("SELECT * FROM traffic_daily") == []
    assert service._store.rows("SELECT * FROM node_traffic_daily") == []
    service.set_privacy(PrivacySettings(usage_history="daily"), author="root")
    service.record_stats("main", first, BEFORE_MIDNIGHT)
    service.record_stats("main", _report(service, 2, alice=(150, 270)), AFTER_MIDNIGHT)
    assert _daily(service) == [(date(2026, 10, 28), 0, 0), (date(2026, 10, 29), 50, 70)]
    assert service.get_user("alice").lifetime_traffic_bytes == 420


def test_disabling_then_enabling_history_does_not_resurrect_deleted_or_off_period_use(service: ControlService) -> None:
    service.set_privacy(PrivacySettings(usage_history="daily"), author="root")
    service.record_stats("main", _report(service, 1, alice=(10, 20)), BEFORE_MIDNIGHT)
    service.set_privacy(PrivacySettings(), author="root")
    service.record_stats("main", _report(service, 2, alice=(30, 60)), AFTER_MIDNIGHT)
    service.set_privacy(PrivacySettings(usage_history="daily"), author="root")
    service.record_stats("main", _report(service, 3, alice=(35, 70)), AFTER_MIDNIGHT)
    assert _daily(service) == [(date(2026, 10, 28), 0, 0), (date(2026, 10, 29), 5, 10)]


def test_daily_growth_uses_the_services_utc_receipt_day_and_not_the_agents_delayed_collection_time(
    service: ControlService,
) -> None:
    service.set_privacy(PrivacySettings(usage_history="daily"), author="root")
    first = _report(service, 1, alice=(10, 20))
    second = _report(service, 2, alice=(17, 31))
    service.record_stats("main", first, BEFORE_MIDNIGHT)
    service.record_stats("main", second, AFTER_MIDNIGHT)
    service.record_stats("main", second, AFTER_MIDNIGHT)  # lost ack
    service.record_stats("main", first, AFTER_MIDNIGHT)  # out-of-order retry
    assert _daily(service) == [(date(2026, 10, 28), 10, 20), (date(2026, 10, 29), 7, 11)]
    assert history_cutoff(1, "2026-10-29T00:01:00+03:00") == "2026-10-28"


def test_nodes_and_counter_epochs_add_only_growth_and_zero_or_forged_users_create_no_history(
    service: ControlService,
) -> None:
    service.set_privacy(PrivacySettings(usage_history="daily"), author="root")
    first = _report(service, 1, alice=(10, 20), bob=(0, 0))
    forged = first.model_copy(
        update={"users": [*first.users, first.users[0].model_copy(update={"user_id": "u0ghost"})]}
    )
    service.record_stats("main", forged, AFTER_MIDNIGHT)
    service.record_stats("edge", _report(service, 1, EPOCH_B, alice=(3, 4)), AFTER_MIDNIGHT)
    service.record_stats("main", _report(service, 1, EPOCH_B, alice=(1, 2)), AFTER_MIDNIGHT)
    service.record_stats("main", _report(service, 2, EPOCH_A, alice=(15, 27)), AFTER_MIDNIGHT)
    assert _daily(service)[-1][1:] == (19, 33)
    assert _daily(service, "bob")[-1][1:] == (0, 0)
    fleet = service.traffic.daily(days=1, now=AFTER_MIDNIGHT)
    assert fleet.daily[0].bytes == 52
    assert {node.node_id: node.daily[0].bytes for node in fleet.nodes} == {"main": 45, "edge": 7}
    assert {row[0] for row in service._db.execute("SELECT user_id FROM traffic_daily")} == {
        service.get_user("alice").user_id
    }


def test_a_counter_regression_rolls_back_history_and_leaves_the_last_ack_unchanged(service: ControlService) -> None:
    service.set_privacy(PrivacySettings(usage_history="daily"), author="root")
    service.record_stats("main", _report(service, 1, alice=(10, 20), bob=(1, 2)), AFTER_MIDNIGHT)
    with pytest.raises(ServiceError, match="went down"):
        service.record_stats("main", _report(service, 2, bob=(9, 9), alice=(9, 20)), AFTER_MIDNIGHT)
    assert _daily(service)[-1][1:] == (10, 20)
    assert _daily(service, "bob")[-1][1:] == (1, 2)
    assert service._store.row("SELECT seq FROM traffic_reports")["seq"] == 1


def test_the_timer_keeps_exactly_the_cap_including_today_and_pruning_again_changes_nothing(
    service: ControlService,
) -> None:
    service.set_privacy(PrivacySettings(usage_history="daily", history_days=7), author="root")
    service.record_stats("main", _report(service, 1, alice=(10, 20)), "2026-10-22T23:59:59Z")
    service.record_stats("main", _report(service, 2, alice=(11, 22)), "2026-10-23T00:00:00Z")
    service.record_stats("main", _report(service, 3, alice=(15, 30)), AFTER_MIDNIGHT)
    for _ in range(2):
        service.enforce(AFTER_MIDNIGHT)
        assert [row[0] for row in service._db.execute("SELECT day FROM traffic_daily ORDER BY day")] == [
            "2026-10-23",
            "2026-10-29",
        ]
        assert [row[0] for row in service._db.execute("SELECT day FROM node_traffic_daily ORDER BY day")] == [
            "2026-10-23",
            "2026-10-29",
        ]
    usage = service.traffic.user_usage("alice", days=730, now=AFTER_MIDNIGHT)
    assert usage.days == usage.history_days == 7 and len(usage.daily) == 7
    assert usage.daily[0].day == date(2026, 10, 23)
    assert [row.uplink_bytes for row in usage.daily] == [1, 0, 0, 0, 0, 0, 4]
    assert service.get_user("alice").lifetime_traffic_bytes == 45


def test_a_traffic_reset_keeps_daily_use_and_a_removed_node_keeps_its_retained_totals(service: ControlService) -> None:
    service.set_privacy(PrivacySettings(usage_history="daily"), author="root")
    service.record_stats("main", _report(service, 1, alice=(10, 20)), AFTER_MIDNIGHT)
    service.reset_user_traffic("alice")
    service.delete_node("main")
    assert _daily(service)[-1][1:] == (10, 20)
    assert service.traffic.daily(days=1, now=AFTER_MIDNIGHT).nodes[0].daily[0].bytes == 30


def test_removing_a_person_drops_their_daily_rows_and_readding_the_name_starts_empty(service: ControlService) -> None:
    service.set_privacy(PrivacySettings(usage_history="daily"), author="root")
    service.record_stats("main", _report(service, 1, alice=(10, 20)), AFTER_MIDNIGHT)
    service.delete_user("alice")
    service.put_user("alice")
    assert _daily(service)[-1][1:] == (0, 0)
    assert service.traffic.daily(days=1, now=AFTER_MIDNIGHT).daily[0].bytes == 30


def test_usage_api_answers_history_off_until_enabled_and_read_tokens_can_read_both_views(tmp_path: Path) -> None:
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN) as admin:
        admin.put_user("alice")
        for call in (lambda: admin.user_usage("alice"), admin.daily_traffic):
            with pytest.raises(ControlServiceHistoryOff, match="history is off"):
                call()
        admin.set_privacy(PrivacySettings(usage_history="daily", history_days=7))
        token = admin.create_token("reader", "read")
        with ControlClient(url, token.token) as reader:
            usage = reader.user_usage("alice", days=90)
            assert usage.days == 7 and all(row.uplink_bytes + row.downlink_bytes == 0 for row in usage.daily)
            fleet = reader.daily_traffic(days=7)
            assert len(fleet.daily) == 7 and fleet.nodes == []
            assert reader._client.get("api/users/missing/usage").status_code == 404
        assert admin._client.get("api/traffic/daily", headers={"Authorization": ""}).status_code == 401


@pytest.mark.parametrize(
    "query", ["days=0", "days=-1", "days=731", "days=", "days=one", "days=7&days=9", "days=" + "9" * 500]
)
def test_usage_days_is_one_bounded_integer_and_bad_queries_never_become_server_errors(
    tmp_path: Path, query: str
) -> None:
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN) as admin:
        admin.put_user("alice")
        admin.set_privacy(PrivacySettings(usage_history="daily"))
        for route in ("api/traffic/daily", "api/users/alice/usage"):
            reply = admin._client.get(route + "?" + query)
            assert reply.status_code == 400 and reply.json()["error"]["code"] == "invalid_request"
