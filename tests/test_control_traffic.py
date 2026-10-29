"""Control service: cumulative per-user traffic from agents' stats reports, counted exactly once."""

from __future__ import annotations

from pathlib import Path

import pytest

from meridian.control.errors import ServiceError
from meridian.control.service import ControlService
from meridian.core.agent_protocol import StatsReport
from tests.support.agent_node import FLEET_ID

EPOCH_A = "5b0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00"
EPOCH_B = "6c1d8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00"


@pytest.fixture
def service(tmp_path: Path) -> ControlService:
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    service.put_user("alice")
    service.put_user("bob")
    return service


def _report(service: ControlService, epoch: str, seq: int, **totals: tuple[int, int]) -> StatsReport:
    users = []
    for name, (up, down) in totals.items():
        user = service.get_user(name)
        assert user is not None
        users.append({"user_id": user.user_id, "uplink_bytes": up, "downlink_bytes": down})
    return StatsReport.model_validate(
        {"type": "stats", "counter_epoch": epoch, "seq": seq, "collected_at": "2026-10-29T12:00:00Z", "users": users}
    )


def _totals(service: ControlService) -> dict[str, tuple[int, int]]:
    return {item.username: (item.uplink_bytes, item.downlink_bytes) for item in service.traffic.summary().users}


def test_growth_is_counted_once_and_resent_reports_only_ack(service: ControlService) -> None:
    first = _report(service, EPOCH_A, 1, alice=(100, 1000))
    assert service.traffic.record("main", first).acked_seq == 1
    assert service.traffic.record("main", first).acked_seq == 1
    service.traffic.record("main", _report(service, EPOCH_A, 2, alice=(150, 1500), bob=(5, 7)))
    stale = service.traffic.record("main", _report(service, EPOCH_A, 1, alice=(999, 999)))

    assert stale.acked_seq == 2
    assert _totals(service) == {"alice": (150, 1500), "bob": (5, 7)}


def test_a_user_missing_from_a_report_keeps_its_total(service: ControlService) -> None:
    service.traffic.record("main", _report(service, EPOCH_A, 1, alice=(10, 20), bob=(1, 2)))
    service.traffic.record("main", _report(service, EPOCH_A, 2, alice=(30, 40)))
    assert _totals(service) == {"alice": (30, 40), "bob": (1, 2)}


def test_a_new_counter_epoch_starts_from_zero_and_the_old_one_never_counts_again(service: ControlService) -> None:
    service.traffic.record("main", _report(service, EPOCH_A, 5, alice=(100, 100)))
    service.traffic.record("main", _report(service, EPOCH_B, 1, alice=(10, 20)))
    late = service.traffic.record("main", _report(service, EPOCH_A, 6, alice=(500, 500)))

    assert (late.counter_epoch, late.acked_seq) == (EPOCH_A, 6)
    assert _totals(service)["alice"] == (110, 120)


def test_a_counter_that_goes_down_is_refused_without_counting_anything(service: ControlService) -> None:
    service.traffic.record("main", _report(service, EPOCH_A, 1, alice=(100, 100), bob=(1, 1)))
    with pytest.raises(ServiceError) as raised:
        service.traffic.record("main", _report(service, EPOCH_A, 2, bob=(9, 9), alice=(50, 200)))
    assert (raised.value.code, raised.value.status) == ("counter_regression", 422)
    assert _totals(service) == {"alice": (100, 100), "bob": (1, 1)}


def test_nodes_add_up_and_last_seen_moves_only_with_traffic(service: ControlService) -> None:
    service.traffic.record("main", _report(service, EPOCH_A, 1, alice=(10, 10), bob=(0, 0)))
    service.traffic.record("edge", _report(service, EPOCH_B, 1, alice=(5, 5)))
    users = {item.username: item for item in service.traffic.summary().users}

    assert (users["alice"].uplink_bytes, users["alice"].downlink_bytes) == (15, 15)
    assert users["alice"].last_seen_at is not None
    assert users["bob"].last_seen_at is None and users["bob"].uplink_bytes == 0


def test_traffic_holds_usernames_and_counters_only(service: ControlService) -> None:
    service.traffic.record("main", _report(service, EPOCH_A, 1, alice=(1, 2)))
    assert set(service.traffic.summary().users[0].model_dump()) == {
        "username",
        "uplink_bytes",
        "downlink_bytes",
        "last_seen_at",
    }
