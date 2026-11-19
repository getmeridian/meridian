"""Backup keys and encrypted snapshots of the service's state (`snapshot.py` is the format).

Every device that manages the fleet registers an X25519 key, and the fleet has one
recovery key whose private half only its owner ever saw. A snapshot is sealed to
all of them: a consistent copy of the database through SQLite's online backup API,
taken under the store's lock, never the live file.

Secrets never ride a snapshot: the CA, signing key, and certificates (the `secrets`
table), agent sessions, enrollment tokens, and hello nonces are deleted from the copy
and the copy is vacuumed, so their pages are gone too. A restore mints a new fleet
CA and every agent enrolls again, so a seized server's keys sign nothing agents
accept. The deployer's own files (`DeployerState`) do ride along, so a snapshot and
the recovery key alone can rebuild the fleet on a machine that has nothing else.
"""

from __future__ import annotations

import hmac
import secrets
import sqlite3
import time
from collections.abc import Callable

from meridian.control import backup_origin
from meridian.control.backup_models import BackupReceipt, BackupStatus, SnapshotSaveProof
from meridian.control.errors import ServiceError
from meridian.control.models import (
    DeployerState,
    DeployerStateDeposit,
    DeviceKeyCreate,
    DeviceKeyList,
    DeviceKeyRecord,
    PrivacySettings,
    RecoveryKeyState,
    RecoveryKeyUpdate,
)
from meridian.control.snapshot import (
    SAVE_PROOF_MEMBER,
    SOURCE_HOST_MEMBER,
    SnapshotHeader,
    SnapshotRecipient,
    encrypt_snapshot,
)
from meridian.control.store import Store
from meridian.control.times import format_timestamp

BACKUP_DEVICES_TABLE = """CREATE TABLE IF NOT EXISTS backup_devices (
    device_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    public_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    revoked_at TEXT
)"""
BACKUP_STATE_TABLE = """CREATE TABLE IF NOT EXISTS backup_state (
    name TEXT PRIMARY KEY,
    body TEXT NOT NULL
)"""
# Tables emptied in every snapshot: a restore regenerates or no longer needs them.
NOT_SNAPSHOTTED = (
    "secrets",
    "backup_receipts",
    "backup_exports",
    "agent_sessions",
    "agent_command_capabilities",
    "commands",
    "enrollment_tokens",
    "hello_nonces",
    # The web owner signs in again after a restore: a seized server held its TOTP secret.
    "ui_owner",
    "ui_sessions",
    "ui_setup_links",
    "ui_passkeys",
    "ui_passkey_owner",
)
_RECOVERY = "recovery_key"
_DEPLOYER = "deployer_state"
_COLUMNS = "device_id, name, public_key, created_at, revoked_at"


def strip_unsnapshotted(db: sqlite3.Connection, *, restoring: bool = False) -> None:
    """Empty every `NOT_SNAPSHOTTED` table `db` has; a snapshot from an older schema may lack some.

    Privacy settings and changes ride every snapshot; devices ride only while on.
    On restore, private fingerprints cannot match the replacement HMAC key and go.
    """
    present = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    row = db.execute("SELECT settings FROM privacy WHERE id = 1").fetchone() if "privacy" in present else None
    privacy = PrivacySettings.model_validate_json(row[0]) if row else PrivacySettings()
    volatile = list(NOT_SNAPSHOTTED)
    if privacy.device_limits == "off" or (restoring and privacy.device_limits == "private"):
        volatile.append("devices")
    elif restoring and "devices" in present:
        # A raise to full may still hold private fingerprints awaiting their next fetch; their key is gone.
        db.execute("DELETE FROM devices WHERE first_seen_at IS NULL")
    if privacy.usage_history == "off":
        volatile.extend(("traffic_daily", "node_traffic_daily"))
    for table in volatile:
        if table in present:
            db.execute(f"DELETE FROM {table}")  # a table named above, never input


class BackupLedger:
    def __init__(self, store: Store, *, clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._clock = clock
        store.create(BACKUP_DEVICES_TABLE)
        store.create(BACKUP_STATE_TABLE)
        # Never snapshotted. A replacement controller cannot accept a previous fleet epoch's receipts.
        with store.transaction():
            if store.secret("backup_receipt_key") is None:
                store.set_secret("backup_receipt_key", secrets.token_bytes(32))

    # -- device keys ---------------------------------------------------------------------

    def register_device(self, request: DeviceKeyCreate) -> DeviceKeyRecord:
        """Add a device's key; the same active key again returns its record, a revoked one is refused."""
        with self._store.transaction():
            row = self._store.row(f"SELECT {_COLUMNS} FROM backup_devices WHERE public_key = ?", (request.public_key,))
            if row is not None:
                if row["revoked_at"] is not None:
                    raise ServiceError("conflict", "This device key was revoked; make a new one.", 409)
                return _device(row)
            device_id = secrets.token_hex(6)
            self._store.db.execute(
                "INSERT INTO backup_devices (device_id, name, public_key, created_at) VALUES (?, ?, ?, ?)",
                (device_id, request.name, request.public_key, self._now()),
            )
        return self._device(device_id)

    def list_devices(self) -> DeviceKeyList:
        rows = self._store.rows(f"SELECT {_COLUMNS} FROM backup_devices ORDER BY created_at, device_id")
        return DeviceKeyList(devices=[_device(row) for row in rows])

    def revoke_device(self, device_id: str) -> DeviceKeyRecord:
        """Leave the device out of every later snapshot; snapshots it already holds stay readable to it."""
        with self._store.transaction():
            self._device(device_id)
            self._store.db.execute(
                "UPDATE backup_devices SET revoked_at = ? WHERE device_id = ? AND revoked_at IS NULL",
                (self._now(), device_id),
            )
        return self._device(device_id)

    # -- recovery key and deployer state ---------------------------------------------------

    def recovery_key(self) -> RecoveryKeyState:
        body = self._state(_RECOVERY)
        return RecoveryKeyState.model_validate_json(body) if body else RecoveryKeyState()

    def set_recovery_key(self, update: RecoveryKeyUpdate) -> RecoveryKeyState:
        state = RecoveryKeyState(public_key=update.public_key, set_at=self._now())
        with self._store.transaction():
            held = self.recovery_key()
            if held.public_key == update.public_key:
                return held
            self._set_state(_RECOVERY, state.model_dump_json())
        return state

    def deposit(self, deposit: DeployerStateDeposit) -> DeployerState:
        """Keep the deployer's newest files; an older generation never replaces a newer one."""
        state = DeployerState(**deposit.model_dump(), deposited_at=self._now())
        with self._store.transaction():
            held = self.deployer_state()
            if held is not None and held.generation > deposit.generation:
                raise ServiceError("conflict", "A newer fleet state is already deposited.", 409)
            if held is not None and held.model_dump(exclude={"deposited_at"}) == deposit.model_dump():
                return held
            self._set_state(_DEPLOYER, state.model_dump_json())
        return state

    def deployer_state(self) -> DeployerState | None:
        body = self._state(_DEPLOYER)
        return DeployerState.model_validate_json(body) if body else None

    # -- snapshots -------------------------------------------------------------------------

    def status(self) -> BackupStatus:
        with self._store.lock:
            receipt = self._store.row(
                "SELECT r.changes, r.saved_at FROM backup_receipts r JOIN backup_devices d USING (device_id) "
                "WHERE d.revoked_at IS NULL ORDER BY r.changes DESC, r.saved_at DESC LIMIT 1"
            )
            return BackupStatus(
                changes=self._changes(),
                last_saved_changes=int(receipt["changes"]) if receipt else None,
                last_saved_at=receipt["saved_at"] if receipt else None,
                has_recovery_key=self.recovery_key().public_key is not None,
                has_deployer_state=self._has_current_deposit(),
            )

    def record_save(self, receipt: BackupReceipt) -> BackupStatus:
        with self._store.transaction():
            device = self._device(receipt.device_id)
            if device.revoked_at is not None:
                raise ServiceError("conflict", "This backup device has been revoked.", 409)
            captured = int(receipt.proof[:16], 16)
            if receipt.changes > self._changes() or not hmac.compare_digest(
                receipt.proof, self._proof(receipt.changes, captured).proof
            ):
                raise ServiceError("conflict", "This receipt does not match a snapshot from this controller.", 409)
            if receipt.changes == self._changes() and not self._has_current_deposit():
                raise ServiceError(
                    "conflict",
                    "The snapshot lacks the current fleet files; pull from the managing computer that applied them.",
                    409,
                )
            held = self._store.row("SELECT changes FROM backup_receipts WHERE device_id=?", (device.id,))
            if held and receipt.changes < held["changes"]:
                raise ServiceError("conflict", "A newer save is already confirmed for this computer.", 409)
            self._store.db.execute(
                "INSERT INTO backup_receipts VALUES (?,?,?,?) ON CONFLICT(device_id) DO UPDATE SET "
                "changes=excluded.changes, saved_at=excluded.saved_at, captured=excluded.captured "
                "WHERE excluded.changes > backup_receipts.changes OR "
                "(excluded.changes = backup_receipts.changes AND excluded.captured > backup_receipts.captured)",
                (device.id, receipt.changes, self._now(), captured),
            )
            return self.status()

    def record_export(self, actor: str) -> None:
        """A bounded local export audit separate from save receipts and the legacy fleet event wire."""
        with self._store.transaction():
            self._store.db.execute("INSERT INTO backup_exports(exported_at, actor) VALUES (?,?)", (self._now(), actor))
            self._store.db.execute("DELETE FROM backup_exports WHERE id <= (SELECT MAX(id) - 100 FROM backup_exports)")

    def _has_current_deposit(self) -> bool:
        # Rebuilding solely from this file must not apply an older topology onto the surviving servers.
        return (
            self._store.row(
                "SELECT 1 FROM backup_state WHERE name=? AND (NOT EXISTS (SELECT 1 FROM apply_fence) OR "
                "json_extract(body,'$.generation') >= "
                "(SELECT json_extract(record,'$.generation') FROM apply_fence WHERE id=1))",
                (_DEPLOYER,),
            )
            is not None
        )

    def _changes(self) -> int:
        return int(self._store.db.execute("SELECT changes FROM backup_progress WHERE id=1").fetchone()[0])

    def _proof(self, changes: int, captured: int) -> SnapshotSaveProof:
        key = self._store.secret("backup_receipt_key")
        assert key is not None
        data = f"backup-save-v1:{self._store.fleet_epoch}:{changes}:{captured}".encode("ascii")
        return SnapshotSaveProof(
            changes=changes, proof=f"{captured:016x}" + hmac.digest(key, data, "sha256").hex()[:48]
        )

    def snapshot(
        self,
        *,
        fleet_id: str,
        fleet_epoch: int,
        version: str,
        for_download: bool = False,
        authorize: Callable[[], object] | None = None,
    ) -> bytes:
        """A consistent, secret-free copy of the database, sealed to every active key."""
        with self._store.lock:
            if authorize:
                authorize()
            has_deposit = self._has_current_deposit()
            if for_download:
                if not self.recovery_key().public_key:
                    raise ServiceError("conflict", "Set a recovery key on a managing computer first.", 409)
                if not has_deposit:
                    raise ServiceError(
                        "conflict",
                        "Run meridian backup pull on the computer that last applied the fleet.",
                        409,
                    )
            proof = self._proof(self._changes(), int(self._clock())) if has_deposit else None
            recipients = [
                SnapshotRecipient(kind="device", id=item.id, public_key=item.public_key)
                for item in self.list_devices().devices
                if item.revoked_at is None
            ]
            recovery = self.recovery_key().public_key
            if recovery:
                recipients.append(SnapshotRecipient(kind="recovery", public_key=recovery))
            if not recipients:
                raise ServiceError("conflict", "No backup key is registered; register a device key first.", 409)
            copy = sqlite3.connect(":memory:", isolation_level=None)
            self._store.db.backup(copy)
        try:
            strip_unsnapshotted(copy)
            copy.execute("VACUUM")
            (revision,) = copy.execute("SELECT COALESCE(MAX(number), 0) FROM revisions").fetchone()
            database = copy.serialize()
        finally:
            copy.close()
        header = SnapshotHeader(
            fleet_id=fleet_id,
            fleet_epoch=fleet_epoch,
            revision=int(revision),
            created_at=self._now(),
            service_version=version[:64],
            recipients=recipients,
        )
        files = {"control.db": database}
        files[SAVE_PROOF_MEMBER] = proof.model_dump_json().encode() if proof else b"null"
        source_host = backup_origin.fleet_host_fingerprint(fleet_id)
        if source_host is not None:
            files[SOURCE_HOST_MEMBER] = source_host.encode("ascii")
        return encrypt_snapshot(header, files)

    # -- storage ---------------------------------------------------------------------------

    def _device(self, device_id: str) -> DeviceKeyRecord:
        row = self._store.row(f"SELECT {_COLUMNS} FROM backup_devices WHERE device_id = ?", (device_id,))
        if row is None:
            raise ServiceError("not_found", f"Unknown device key {device_id!r}.", 404)
        return _device(row)

    def _state(self, name: str) -> str | None:
        row = self._store.row("SELECT body FROM backup_state WHERE name = ?", (name,))
        return str(row["body"]) if row is not None else None

    def _set_state(self, name: str, body: str) -> None:
        self._store.db.execute(
            "INSERT INTO backup_state VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET body = excluded.body", (name, body)
        )

    def _now(self) -> str:
        return format_timestamp(self._clock())


def _device(row: sqlite3.Row) -> DeviceKeyRecord:
    return DeviceKeyRecord(
        id=str(row["device_id"]),
        name=str(row["name"]),
        public_key=str(row["public_key"]),
        created_at=str(row["created_at"]),
        revoked_at=row["revoked_at"],
    )
