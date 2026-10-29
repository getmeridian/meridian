"""Backup keys, deployer state, encrypted snapshots, and the state a restore starts from."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from meridian.control.client import ControlClient, ControlServiceConflict, ControlServiceScopeError
from meridian.control.enrollment import NodeAuthority
from meridian.control.models import DeployerStateDeposit, DeviceKeyCreate, RevisionProposal
from meridian.control.restore import RestoreError, read_deployer_state, stage_restored_database
from meridian.control.service import ControlService
from meridian.control.snapshot import SnapshotError, decrypt_snapshot, generate_key_pair
from meridian.control.store import Store
from meridian.core.agent_protocol import EnrollRequest
from tests.support.agent_node import FakeNode
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID, serve_control
from tests.test_control_service import _spec


@pytest.fixture
def admin(tmp_path: Path):  # type: ignore[no-untyped-def]
    with serve_control(tmp_path / "state") as url, ControlClient(url, ADMIN_TOKEN) as client:
        yield client


def _deposit(generation: int, marker: str = "a") -> DeployerStateDeposit:
    return DeployerStateDeposit(cluster_yml=f"version: 3\n# {marker}\n", servers_json="[]", generation=generation)


def test_a_snapshot_needs_a_key_to_seal_to(admin: ControlClient) -> None:
    with pytest.raises(ControlServiceConflict, match="No backup key"):
        admin.snapshot()


def test_a_snapshot_opens_with_each_active_key_and_never_a_revoked_one(admin: ControlClient) -> None:
    laptop, phone, recovery = generate_key_pair(), generate_key_pair(), generate_key_pair()
    admin.register_device_key("laptop", laptop.public)
    revoked = admin.register_device_key("old phone", phone.public)
    admin.revoke_device_key(revoked.id)
    admin.set_recovery_key(recovery.public)

    blob = admin.snapshot()

    for secret in (laptop.secret, recovery.secret):
        assert decrypt_snapshot(blob, [secret]).header.fleet_id == FLEET_ID
    with pytest.raises(SnapshotError):
        decrypt_snapshot(blob, [phone.secret])


def test_registering_the_same_key_again_keeps_one_record(admin: ControlClient) -> None:
    key = generate_key_pair().public

    first = admin.register_device_key("laptop", key)

    assert admin.register_device_key("laptop", key) == first
    assert [device.id for device in admin.device_keys()] == [first.id]


def test_a_revoked_key_stays_revoked(admin: ControlClient) -> None:
    key = generate_key_pair().public
    admin.revoke_device_key(admin.register_device_key("laptop", key).id)

    with pytest.raises(ControlServiceConflict, match="revoked"):
        admin.register_device_key("laptop", key)


def test_the_service_keeps_only_the_recovery_keys_public_half(admin: ControlClient, tmp_path: Path) -> None:
    recovery = generate_key_pair()
    assert admin.recovery_key().public_key is None

    state = admin.set_recovery_key(recovery.public)

    assert state.public_key == recovery.public and state.set_at
    raw = (tmp_path / "state" / "control.db").read_bytes() + (tmp_path / "state" / "control.db-wal").read_bytes()
    assert recovery.secret.encode() not in raw


def test_backup_routes_need_admin(admin: ControlClient) -> None:
    reader = admin.create_token("reader", "approve")
    with ControlClient(admin.url, reader.token) as client:
        for call in (client.snapshot, client.device_keys, client.recovery_key):
            with pytest.raises(ControlServiceScopeError):
                call()


def test_an_older_deployer_state_never_replaces_a_newer_one(admin: ControlClient) -> None:
    admin.deposit_deployer_state(_deposit(4, "four"))

    with pytest.raises(ControlServiceConflict, match="newer"):
        admin.deposit_deployer_state(_deposit(3, "three"))
    admin.deposit_deployer_state(_deposit(4, "four again"))


def test_the_snapshot_is_a_consistent_secret_free_copy(admin: ControlClient, tmp_path: Path) -> None:
    device = generate_key_pair()
    admin.register_device_key("laptop", device.public)
    admin.put_user("alice")
    admin.put_node("main", _spec())
    admin.grant_enrollment("main")
    admin.deposit_deployer_state(_deposit(7))

    snapshot = decrypt_snapshot(admin.snapshot(), [device.secret])
    copy = sqlite3.connect(":memory:")
    copy.deserialize(snapshot.database)

    assert copy.execute("SELECT username FROM users").fetchall() == [("alice",)]
    assert copy.execute("SELECT node_id FROM nodes").fetchall() == [("main",)]
    for table in ("secrets", "enrollment_tokens", "agent_sessions", "hello_nonces"):
        assert copy.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,), table
    signing_key = Store(tmp_path / "state" / "control.db").secret("signing_key")
    assert signing_key is not None and signing_key not in snapshot.database
    deposited = read_deployer_state(snapshot.database)
    assert deposited is not None and deposited.generation == 7


def test_the_header_names_the_epoch_and_newest_revision(admin: ControlClient) -> None:
    device = generate_key_pair()
    admin.register_device_key("laptop", device.public)
    admin.propose_revision(RevisionProposal(intent={}, plan_hash="a" * 64, review_hash="b" * 64))

    header = decrypt_snapshot(admin.snapshot(), [device.secret]).header

    assert (header.fleet_epoch, header.revision) == (1, 1)
    assert [recipient.kind for recipient in header.recipients] == ["device"]


# -- the restored service's starting state --------------------------------------------------


def _enrolled_service(path: Path) -> tuple[ControlService, FakeNode]:
    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    service.put_user("alice")
    service.put_node_spec("main", _spec())
    node = FakeNode()
    grant = service.grant_enrollment("main")
    response = service.authority.enroll(EnrollRequest.model_validate(node.enroll_request(grant.enrollment_token)))
    node.certificate_pem = response.certificate_pem
    return service, node


def test_staging_bumps_the_epoch_mints_a_new_ca_and_signs_documents_again(tmp_path: Path) -> None:
    service, _node = _enrolled_service(tmp_path / "live.db")
    device = generate_key_pair()
    service.backups.register_device(DeviceKeyCreate(name="laptop", public_key=device.public))
    old_ca = service.authority.ca_sha256
    old_revision = service.node_status("main").revision
    snapshot = decrypt_snapshot(service.snapshot(), [device.secret])

    staged = stage_restored_database(snapshot.database, tmp_path / "staged.db", fleet_id=FLEET_ID, version="4.0.0")

    assert staged.fleet_epoch == 2
    assert not (tmp_path / "staged.db-wal").exists()
    restored = ControlService(tmp_path / "staged.db", fleet_id=FLEET_ID, version="4.0.0")
    assert restored.fleet_epoch == 2
    assert restored.authority.ca_sha256 != old_ca
    status = restored.node_status("main")
    assert status.fleet_epoch == 2 and status.revision == old_revision + 1
    document = json.loads(restored._node("main")["document"])  # noqa: SLF001
    assert document["fleet_epoch"] == 2
    assert document["signature"]["key_id"] == restored.authority.service_key_id
    assert [user.username for user in restored.list_users()] == ["alice"]


def test_staging_revokes_every_node_certificate(tmp_path: Path) -> None:
    service, _node = _enrolled_service(tmp_path / "live.db")
    service.close()

    stage_restored_database((tmp_path / "live.db").read_bytes(), tmp_path / "staged.db", fleet_id=FLEET_ID, version="4")

    store = Store(tmp_path / "staged.db")
    NodeAuthority(store, FLEET_ID)
    assert store.rows("SELECT sha256 FROM node_certificates WHERE revoked_at IS NULL") == []


def test_staging_refuses_another_fleets_database(tmp_path: Path) -> None:
    service, _node = _enrolled_service(tmp_path / "live.db")
    service.close()

    with pytest.raises(RestoreError, match="another fleet"):
        stage_restored_database(
            (tmp_path / "live.db").read_bytes(),
            tmp_path / "staged.db",
            fleet_id="0f1e2d3c-4b5a-4968-8776-655443322111",
            version="4.0.0",
        )
