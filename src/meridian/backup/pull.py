"""Pull the control service's newest snapshot into this fleet context's `snapshots/`.

One pull: make sure this device's key is registered, deposit this machine's fleet
files (`cluster.yml` without its admin token, and `servers.json`) so the snapshot
can rebuild the fleet on its own, fetch the sealed snapshot, check that this
device can open it, and keep the newest `KEEP` files.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from meridian import config
from meridian.backup import BackupError
from meridian.backup.keys import create_device_key, device_name, load_device_key, snapshots_dir, write_private
from meridian.cluster import ClusterConfig, ControlRestore
from meridian.cluster_persistence import dump_cluster
from meridian.control.backup_models import BackupReceipt
from meridian.control.backup_origin import fleet_host_fingerprint
from meridian.control.client import ControlClient, ControlServiceConflict, ControlServiceError
from meridian.control.models import DeployerStateDeposit
from meridian.control.snapshot import KeyPair, Snapshot, SnapshotError, SnapshotHeader, decrypt_snapshot, read_header
from meridian.core.errors import LocalStateError
from meridian.host_runtime import CONTROL_SERVICE_STATE_DIR
from meridian.servers import ServerRegistry, address_is_local

KEEP = 5
SUFFIX = ".snapshot"
TIMEOUT = 60.0
ReceiptState = Literal["confirmed", "unavailable", "local", "unsupported", "incomplete"]


@dataclass(frozen=True)
class PulledSnapshot:
    path: Path
    header: SnapshotHeader
    registered: bool  # this pull registered the device's key
    deposit_refused: bool  # the service holds a newer deployer state than this machine's

    receipt: ReceiptState

    @property
    def has_recovery_key(self) -> bool:
        return any(recipient.kind == "recovery" for recipient in self.header.recipients)


def backup_client(cluster: ClusterConfig, *, timeout: float = TIMEOUT, report_mutations: bool = False) -> ControlClient:
    """A client for backup calls; a pull's own writes never trigger another pull."""
    if not cluster.runs_control_service or not cluster.is_configured:
        raise BackupError(
            "Only a deployed fleet on Meridian's control service has snapshots.",
            hint="Deploy the fleet first, or move a Remnawave fleet across with `meridian migrate`.",
            category="user",
        )
    return ControlClient(cluster.panel.url, cluster.panel.api_token, timeout=timeout, report_mutations=report_mutations)


def pull_snapshot(cluster: ClusterConfig, *, name: str = "") -> PulledSnapshot:
    key = load_device_key() or create_device_key()
    with backup_client(cluster) as client:
        registered = _register(client, key, name or device_name())
        deposit_refused = False
        try:
            client.deposit_deployer_state(deployer_state(cluster))
        except ControlServiceConflict:
            deposit_refused = True
        blob = client.snapshot()
        opened = decrypt_snapshot(blob, [key.secret])  # authenticate both content and header before keeping the copy
        header = opened.header
        if header.fleet_id != cluster.fleet_id:
            raise BackupError(
                "The control service answered with another fleet's snapshot.",
                hint="Check that cluster.yml belongs to this fleet's control server.",
                category="user",
            )
        proof = opened.save_proof
        path = save_snapshot(blob, header, changes=proof.changes if proof else None, incomplete=opened.incomplete)
        receipt: ReceiptState = "unsupported"
        if proof is not None:
            try:
                separate = _off_control_host(cluster, opened.source_host)
            except (LocalStateError, OSError):
                separate = None  # a damaged server registry cannot attest that this is a separate device
            if separate is None:
                receipt = "unavailable"
            elif not separate:
                receipt = "local"
            else:
                device = next(
                    item for item in header.recipients if item.kind == "device" and item.public_key == key.public
                )
                try:
                    client.confirm_saved_snapshot(
                        BackupReceipt(device_id=device.id, changes=proof.changes, proof=proof.proof)
                    )
                except (ControlServiceError, ValueError):
                    receipt = "unavailable"  # the file was saved; a refused/lost receipt must not undo that fact
                else:
                    receipt = "confirmed"
        elif opened.incomplete:
            receipt = "incomplete"
    return PulledSnapshot(path, header, registered, deposit_refused, receipt)


def _off_control_host(cluster: ClusterConfig, source_host: str | None) -> bool | None:
    # Prefer the source of the exact encrypted snapshot; the stored server address and
    # profile may be old, and a custom control service need not use the standard directory.
    local_tag = fleet_host_fingerprint(cluster.fleet_id)
    if (source_host and source_host == local_tag) or address_is_local(cluster.panel.server_ip):
        return False
    intent = cluster.topology_intent
    entry = ServerRegistry(config.SERVER_PROFILES_FILE).find(intent.control.server_ref) if intent else None
    if entry is not None and entry.runs_here():
        return False
    if source_host and local_tag and source_host[0] == local_tag[0]:
        return True
    if Path(CONTROL_SERVICE_STATE_DIR).is_dir():
        return False
    # Supported deployed control servers run Linux. A Linux CLI without comparable
    # host evidence, for example a restricted container, keeps the file without attesting.
    return None if sys.platform.startswith("linux") else True


def deployer_state(cluster: ClusterConfig) -> DeployerStateDeposit:
    """This machine's fleet files, without the secrets a restore replaces or no longer needs.

    The deposit sits in control.db as it is, so it keeps no admin token (a restore mints one),
    no migration token, and none of a Remnawave panel's leftovers (its password, its Telegram
    bot). An unfinished restore is this machine's progress, not the fleet's: carried into a
    fresh context, it would hold that context to a target server that may be gone.
    """
    copy = cluster.clone()
    copy.panel.api_token = ""
    copy.panel.admin_pass = ""
    copy.telegram = None
    if copy.control_migration is not None:
        copy.control_migration.service_token = ""
    copy.control_restore = finished_restore(copy.control_restore)
    try:
        servers = config.SERVER_PROFILES_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        servers = "[]"
    return DeployerStateDeposit(
        cluster_yml=dump_cluster(copy), servers_json=servers or "[]", generation=cluster.active_generation
    )


def finished_restore(restore: ControlRestore | None) -> ControlRestore | None:
    """A restore record worth carrying to another machine: only a finished one (its epoch still counts)."""
    return restore if restore is not None and restore.step == "done" else None


def save_snapshot(blob: bytes, header: SnapshotHeader, *, changes: int | None = None, incomplete: bool = False) -> Path:
    stamp = "".join(character for character in header.created_at if character.isdigit())
    # Exports can share a whole-second timestamp/revision even across panel edits. Keep both;
    # retaining the higher change count first prevents a slower earlier pull from replacing it.
    if changes is None:
        version = f"e{header.fleet_epoch}-r{header.revision}"
    else:
        version = f"v{header.fleet_epoch:016d}-s{changes:016d}-r{header.revision}"
    # Several pulls on a stale manager must not evict its last independent recovery copy.
    directory = snapshots_dir() / "partial" if incomplete else snapshots_dir()
    path = directory / f"{stamp[:8]}-{stamp[8:14]}-{version}{SUFFIX}"
    write_private(path, blob)
    for stale in _snapshots_in(directory)[KEEP:]:
        stale.unlink(missing_ok=True)
    return path


def saved_snapshots() -> list[Path]:
    """This context's snapshots, newest first (names sort by time)."""
    return _snapshots_in(snapshots_dir())


def _snapshots_in(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted((path for path in directory.iterdir() if path.name.endswith(SUFFIX)), reverse=True)


def newer_saved_snapshot(snapshot: Snapshot, keys: list[str], chosen: Path) -> Path | None:
    """Find newer state, including an edit made in the same second and topology revision."""

    def order(item: SnapshotHeader) -> tuple[int, int, datetime]:
        return item.fleet_epoch, item.revision, datetime.fromisoformat(item.created_at.replace("Z", "+00:00"))

    for path in saved_snapshots():
        if path.resolve() == chosen.resolve():
            continue
        try:
            blob = path.read_bytes()
            saved = read_header(blob)
            if saved.fleet_id != snapshot.header.fleet_id:
                continue
            if order(saved) > order(snapshot.header):
                return path
            if order(saved) == order(snapshot.header) and snapshot.save_proof:
                candidate = decrypt_snapshot(blob, keys).save_proof
                if candidate and candidate.changes > snapshot.save_proof.changes:
                    return path
        except (OSError, SnapshotError, ValueError):
            continue
    return None


def _register(client: ControlClient, key: KeyPair, name: str) -> bool:
    for device in client.device_keys():
        if device.public_key != key.public:
            continue
        if device.revoked_at is not None:
            raise BackupError(
                "This device's backup key was revoked, so snapshots are no longer sealed to it.",
                hint=f"If that was a mistake, move {config.HOME_DISPLAY}/device.key aside and run "
                "`meridian backup pull` to register a new key.",
                category="user",
            )
        return False
    client.register_device_key(name, key.public)
    return True
