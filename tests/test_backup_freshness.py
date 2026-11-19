"""Restore a real panel edit with the recovery key; only a verified CLI save changes the receipt."""

import sqlite3
from pathlib import Path

import pytest

from meridian.control import backup_origin
from meridian.control.backup_models import BackupReceipt
from meridian.control.errors import ServiceError
from meridian.control.models import (
    ApplyFenceRecord,
    ApplyFenceSwap,
    DeployerStateDeposit,
    DeviceKeyCreate,
    NodeSpec,
    RecoveryKeyUpdate,
    UserUpdate,
)
from meridian.control.owner import CONFIRM_SECONDS
from meridian.control.restore import stage_restored_database
from meridian.control.service import ControlService
from meridian.control.snapshot import decrypt_snapshot, generate_key_pair
from meridian.core.agent_protocol import StatsReport
from tests.test_control_ui_http import ADMIN_TOKEN, FLEET_ID, Fleet, fleet

__all__ = ["fleet"]


def test_only_confirmed_saves_count_and_an_owner_can_restore_the_later_panel_edit(
    fleet: Fleet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fleet.set_up()
    monkeypatch.setattr(backup_origin, "fleet_host_fingerprint", lambda _fleet_id: "b" * 64)
    backups = fleet.service.backups
    device, recovery = generate_key_pair(), generate_key_pair()
    key = backups.register_device(DeviceKeyCreate(name="managing computer", public_key=device.public))
    backups.set_recovery_key(RecoveryKeyUpdate(public_key=recovery.public))
    deposit = DeployerStateDeposit(cluster_yml="version: 3\n", servers_json="[]", generation=2)
    backups.deposit(deposit)
    before = backups.status()
    assert before.last_saved_at is None

    fleet.clock.now += 1
    backups.deposit(deposit)
    backups.register_device(DeviceKeyCreate(name="same device", public_key=device.public))
    backups.set_recovery_key(RecoveryKeyUpdate(public_key=recovery.public))
    assert backups.status() == before
    saved = decrypt_snapshot(fleet.service.snapshot(), [device.secret]).save_proof
    assert saved and saved.changes == before.changes
    receipt = BackupReceipt(device_id=key.id, **saved.model_dump()).model_dump()
    assert fleet.ui("POST", "/api/backup/receipts", receipt).status == 403
    assert fleet.call("POST", "/api/backup/receipts", receipt, bearer=ADMIN_TOKEN).status == 200
    confirmed_at = backups.status().last_saved_at

    fleet.clock.now += 3
    assert fleet.call("POST", "/api/backup/receipts", receipt, bearer=ADMIN_TOKEN).body["last_saved_at"] == confirmed_at
    newer = decrypt_snapshot(fleet.service.snapshot(), [device.secret]).save_proof
    assert newer and newer.changes == saved.changes
    newer_receipt = BackupReceipt(device_id=key.id, **newer.model_dump())
    assert backups.record_save(newer_receipt).last_saved_at != confirmed_at
    confirmed_at = backups.status().last_saved_at
    assert fleet.ui("POST", "/api/users", {"username": "sample", "display_name": "Recoverable name"}).status == 200
    status = fleet.ui("GET", "/api/backup/status").body
    assert status["changes"] == before.changes + 1 and status["last_saved_changes"] == before.changes

    exported = fleet.ui("POST", "/api/backup/download")
    assert exported.status == 200
    assert exported.headers["Cache-Control"] == "no-store"
    assert exported.headers["Content-Disposition"] == 'attachment; filename="backup.snapshot"'
    assert backups.status().last_saved_at == confirmed_at
    assert fleet.ui("GET", "/api/backup/status").body == status
    exported_by = fleet.service._store.row("SELECT actor FROM backup_exports")
    assert exported_by and exported_by["actor"] == "owner"
    restored = decrypt_snapshot(exported.raw, [recovery.secret])
    assert restored.source_host == "b" * 64 and b"b" * 64 not in exported.raw
    assert restored.save_proof and restored.save_proof.changes == status["changes"]

    path = tmp_path / "replacement" / "control.db"
    stage_restored_database(restored.database, path, fleet_id=FLEET_ID, version="4.0.0")
    replacement = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    try:
        person = replacement.get_user("sample")
        assert (
            person and person.display_name == "Recoverable name" and replacement.backups.status().last_saved_at is None
        )
        with pytest.raises(ServiceError, match="does not match"):
            replacement.backups.record_save(newer_receipt)
    finally:
        replacement.close()


def test_download_needs_existing_keys_deposit_csrf_and_a_current_confirmation(fleet: Fleet) -> None:
    fleet.set_up()
    key = generate_key_pair()
    assert fleet.ui("POST", "/api/backup/download").status == 409
    assert fleet.ui("POST", "/api/backup/devices", {"public_key": key.public, "name": "bad"}).status == 403
    assert fleet.ui("PUT", "/api/backup/recovery-key", {"public_key": key.public}).status == 403
    fleet.service.backups.set_recovery_key(RecoveryKeyUpdate(public_key=key.public))
    assert fleet.ui("POST", "/api/backup/download").status == 409
    fleet.service.backups.deposit(DeployerStateDeposit(cluster_yml="version: 3\n", servers_json="[]", generation=1))
    assert fleet.call("POST", "/api/backup/download", session=True).status == 403
    assert fleet.ui("POST", "/api/backup/download", {"public_key": key.public}).status == 400
    fleet.clock.now += CONFIRM_SECONDS + 1
    reply = fleet.ui("POST", "/api/backup/download")
    assert reply.status == 403 and reply.body["error"]["code"] == "reauth_required"
    assert fleet.service.backups.status().last_saved_at is None


def test_changes_ignore_traffic_token_reads_retries_and_rolled_back_edits(fleet: Fleet) -> None:
    service = fleet.service
    key = generate_key_pair()
    dev = service.backups.register_device(DeviceKeyCreate(name="test", public_key=key.public))
    person = service.put_user("sample")
    service.backups.deposit(DeployerStateDeposit(cluster_yml="version: 3\n", servers_json="[]", generation=1))
    spec = NodeSpec(resources=[])
    settings = service.settings()
    service.put_settings(settings)
    service.put_node_spec("edge", spec)
    fence = ApplyFenceRecord(fleet_id=FLEET_ID, generation=1, plan_hash="a" * 64, lease_holder="local")
    service.revisions.swap_fence(ApplyFenceSwap(expected_version=0, record=fence))
    baseline = service.backups.status().changes
    for version in (1, 2):
        service.put_settings(settings)
        service.put_node_spec("edge", spec)
        service.node_seen("edge", "test-agent")
        service.revisions.swap_fence(
            ApplyFenceSwap(
                expected_version=version,
                record=fence.model_copy(update={"lease_expires_at": f"2034-01-01T00:00:0{version}Z"}),
            )
        )
    service.put_user("sample")
    service.record_stats(
        "edge",
        StatsReport.model_validate(
            {
                "counter_epoch": "00000000-0000-4000-8000-000000000001",
                "seq": 1,
                "collected_at": service.now(),
                "users": [{"user_id": person.user_id, "uplink_bytes": 1000, "downlink_bytes": 1000}],
            }
        ),
    )
    assert service.backups.status().changes == baseline
    service.update_user("sample", UserUpdate(note="new", tag="FAMILY"))
    assert service.backups.status().changes == baseline + 1
    service.update_user("sample", UserUpdate(note="new", tag="FAMILY"))
    assert service.backups.status().changes == baseline + 1
    # Several columns changed before this transaction was abandoned.
    store = service._store
    with pytest.raises(RuntimeError, match="abort"):
        with store.transaction():
            store.db.execute("UPDATE users SET tag='OTHER', note='discarded' WHERE username='sample'")
            raise RuntimeError("abort")
    assert service.backups.status().changes == baseline + 1
    proof = decrypt_snapshot(service.snapshot(), [key.secret]).save_proof
    assert proof
    receipt = BackupReceipt(device_id=dev.id, **proof.model_dump())
    for bad in (receipt.model_copy(update={"changes": 100000}), receipt.model_copy(update={"proof": "0" * 64})):
        with pytest.raises(ServiceError):
            service.backups.record_save(bad)
    assert service.backups.status().last_saved_at is None
    service.backups.record_save(receipt)
    service.backups.revoke_device(dev.id)
    assert service.backups.status().last_saved_at is None
    with pytest.raises(ServiceError, match="revoked"):
        service.backups.record_save(receipt)


def test_signout_while_the_export_is_prepared_withholds_bytes_and_writes_no_audit(fleet: Fleet, monkeypatch) -> None:
    fleet.set_up()
    service = fleet.service
    recovery = generate_key_pair()
    service.backups.set_recovery_key(RecoveryKeyUpdate(public_key=recovery.public))
    service.backups.deposit(DeployerStateDeposit(cluster_yml="version: 3\n", servers_json="[]", generation=0))
    export = service.snapshot

    def sign_out_after_export(**kwargs):
        blob = export(**kwargs)
        session = service.owner.authenticate(fleet.cookie)
        service.owner.logout(session)
        return blob

    monkeypatch.setattr(service, "snapshot", sign_out_after_export)
    response = fleet.ui("POST", "/api/backup/download")
    assert response.status == 401 and response.headers["Content-Type"] == "application/json"
    assert not service._store.rows("SELECT id FROM backup_exports")


def test_delayed_receipts_confirm_only_the_captured_version(fleet: Fleet) -> None:
    service = fleet.service
    key = generate_key_pair()
    device = service.backups.register_device(DeviceKeyCreate(name="computer", public_key=key.public))

    service.backups.deposit(DeployerStateDeposit(cluster_yml="version: 3\n", servers_json="[]", generation=0))

    def receipt() -> BackupReceipt:
        proof = decrypt_snapshot(service.snapshot(), [key.secret]).save_proof
        assert proof
        return BackupReceipt(device_id=device.id, **proof.model_dump())

    previous = receipt()
    service.put_user("changed")
    after = receipt()
    assert after.changes == previous.changes + 1
    observed = service.backups.record_save(previous)
    assert observed.last_saved_changes == previous.changes and observed.changes == after.changes
    assert service.backups.record_save(after).last_saved_changes == after.changes
    with pytest.raises(ServiceError, match="newer save"):
        service.backups.record_save(previous)


def test_a_beta18_upgrade_never_assumes_existing_backups_are_current_and_new_receipts_survive_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "control.db"
    key = generate_key_pair()
    svc = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    device = svc.backups.register_device(DeviceKeyCreate(name="existing", public_key=key.public))
    svc.put_user("sample")
    svc.backups.deposit(DeployerStateDeposit(cluster_yml="version: 3\n", servers_json="[]", generation=0))
    svc.close()

    db = sqlite3.connect(path)
    try:
        with db:
            for table in ("backup_exports", "backup_receipts", "backup_progress"):
                db.execute(f"DROP TABLE {table}")
            db.execute("UPDATE meta SET value='26' WHERE key='schema_version'")
            db.execute("DELETE FROM secrets WHERE name='backup_receipt_key'")
    finally:
        db.close()

    svc = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    try:
        status = svc.backups.status()
        assert status.changes == 0 and status.last_saved_at is None
        new = decrypt_snapshot(svc.snapshot(), [key.secret]).save_proof
        assert new
        saved = svc.backups.record_save(BackupReceipt(device_id=device.id, **new.model_dump()))
    finally:
        svc.close()

    svc = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    try:
        assert svc.backups.status() == saved
        svc.update_user("sample", UserUpdate(note="edit after restart"))
        after = svc.backups.status()
        assert after.changes == 1 and after.last_saved_changes == 0
    finally:
        svc.close()


def test_a_stale_topology_deposit_cannot_masquerade_as_a_complete_recovery_file(fleet: Fleet) -> None:
    service = fleet.service
    key = generate_key_pair()
    service.backups.register_device(DeviceKeyCreate(name="computer", public_key=key.public))
    service.backups.set_recovery_key(RecoveryKeyUpdate(public_key=generate_key_pair().public))
    deposit = DeployerStateDeposit(cluster_yml="version: 3\n", servers_json="[]", generation=1)
    service.backups.deposit(deposit)
    service.revisions.swap_fence(
        ApplyFenceSwap(expected_version=0, record=ApplyFenceRecord(fleet_id=FLEET_ID, generation=2))
    )
    assert not service.backups.status().has_deployer_state
    with pytest.raises(ServiceError, match="backup pull"):
        service.snapshot(for_download=True)
    # The legacy CLI can still fetch a useful partial file, but cannot report it as a current recovery copy.
    assert decrypt_snapshot(service.snapshot(), [key.secret]).save_proof is None
    service.backups.deposit(deposit.model_copy(update={"generation": 2}))
    assert service.backups.status().has_deployer_state and service.snapshot(for_download=True)
