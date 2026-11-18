"""Reproduce the surviving node's exact usage replay against a real restored service."""

import sqlite3
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from meridian.control.models import DeviceKeyCreate
from meridian.control.restore import stage_restored_database
from meridian.control.service import ControlService
from meridian.control.snapshot import decrypt_snapshot, generate_key_pair
from meridian.control_access import ServiceAccessPanel
from meridian.core.agent_protocol import StatsReport
from meridian.core.clients import build_client_detail
from tests.realvm import handover_traffic as witness
from tests.realvm.upgrade_support import SubscriptionSnapshot, VerificationError, compare_subscriptions
from tests.support.control_service import FLEET_ID


def capture(service: ControlService):
    people, docs = {}, {}
    with ServiceAccessPanel("http://127.0.0.1:1", "fixture") as panel:
        for name in ("bob", "default"):
            person = service.get_user(name)
            assert person is not None
            people[name] = build_client_detail(panel._user(person))
            sub = service.subscription(person.sub_token, user_agent="Happ/1.0")
            assert sub.status == 200
            docs[(name, "Happ")] = SubscriptionSnapshot(sub.body.encode(), sub.headers["subscription-userinfo"])
    return people, docs


def test_restore_replays_only_the_original_persons_measured_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def report(path):
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
            return witness.Report(*db.execute(witness.QUERY).fetchone())

    live = tmp_path / "before.db"
    service = ControlService(live, fleet_id=FLEET_ID, version="4")
    try:
        bob = service.put_user("bob")
        service.put_user("default")
        key = generate_key_pair()
        service.backups.register_device(DeviceKeyCreate(name="fixture", public_key=key.public))

        def stats(seq, up, down):
            return StatsReport.model_validate(
                {
                    "type": "stats",
                    "seq": seq,
                    "counter_epoch": "5b0c8a52-6c1d-4e8b-9a7f-2b5d4c3e1f00",
                    "collected_at": "2026-11-18T12:00:00Z",
                    "users": [{"user_id": bob.user_id, "uplink_bytes": up, "downlink_bytes": down}],
                }
            )

        service.traffic.record("exit", stats(1, 10, 40))
        start = report(live)
        people, docs = capture(service)
        snapshot = decrypt_snapshot(service.snapshot(), [key.secret])
        post_probe = stats(3, 267, 4136)
        service.traffic.record("exit", post_probe)
        end = report(live)
    finally:
        service.close()

    restored = tmp_path / "restored.db"
    stage_restored_database(
        snapshot.database, restored, fleet_id=FLEET_ID, version="4", minimum_epoch=snapshot.header.fleet_epoch
    )
    service = ControlService(restored, fleet_id=FLEET_ID, version="4")
    try:
        assert report(restored) == start
        service.traffic.record("exit", post_probe)
        assert report(restored) == end
        actual_people, actual_docs = capture(service)
        assert compare_subscriptions(docs, actual_docs)  # the old pre-probe oracle would reject this correct restore
        expected_people, expected_docs = witness.replay_expectations(start, end, people, docs)
        assert not compare_subscriptions(expected_docs, actual_docs)
        for name in people:
            assert expected_people[name].id == actual_people[name].id
            assert expected_people[name].traffic_used_bytes == actual_people[name].traffic_used_bytes
            assert expected_people[name].lifetime_traffic_bytes == actual_people[name].lifetime_traffic_bytes
        assert expected_people["default"] == people["default"]
        assert expected_docs["default", "Happ"] == docs["default", "Happ"]
        assert expected_docs["bob", "Happ"].body == docs["bob", "Happ"].body
        with pytest.raises(VerificationError, match="did not account"):
            witness.replay_expectations(start, replace(end, epoch="unrelated"), people, docs)
        with pytest.raises(VerificationError, match="did not account"):
            witness.replay_expectations(start, replace(start, seq=end.seq), people, docs)
        run = Mock()
        run.wait_for.side_effect = lambda _label, predicate, **kwargs: predicate()
        monkeypatch.setattr(witness, "read_report", lambda _run: replace(end, upload=end.upload + 1))
        with pytest.raises(VerificationError, match="per-direction"):
            witness.await_report(run, replay=end)
    finally:
        service.close()
