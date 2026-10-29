"""Pull the control service's newest snapshot into this fleet context's `snapshots/`.

One pull: make sure this device's key is registered, deposit this machine's fleet
files (`cluster.yml` without its admin token, and `servers.json`) so the snapshot
can rebuild the fleet on its own, fetch the sealed snapshot, check that this
device can open it, and keep the newest `KEEP` files.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from meridian import config
from meridian.backup import BackupError
from meridian.backup.keys import create_device_key, device_name, load_device_key, snapshots_dir, write_private
from meridian.cluster import ClusterConfig, ControlRestore
from meridian.cluster_persistence import dump_cluster
from meridian.control.client import ControlClient, ControlServiceConflict
from meridian.control.models import DeployerStateDeposit
from meridian.control.snapshot import KeyPair, SnapshotError, SnapshotHeader, decrypt_snapshot, read_header

KEEP = 5
SUFFIX = ".snapshot"
TIMEOUT = 60.0


@dataclass(frozen=True)
class PulledSnapshot:
    path: Path
    header: SnapshotHeader
    registered: bool  # this pull registered the device's key
    deposit_refused: bool  # the service holds a newer deployer state than this machine's

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
    header = read_header(blob)
    if header.fleet_id != cluster.fleet_id:
        raise BackupError(
            "The control service answered with another fleet's snapshot.",
            hint="Check that cluster.yml belongs to this fleet's control server.",
            category="user",
        )
    decrypt_snapshot(blob, [key.secret])  # a snapshot this device cannot open is no backup
    path = save_snapshot(blob, header)
    return PulledSnapshot(path=path, header=header, registered=registered, deposit_refused=deposit_refused)


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


def save_snapshot(blob: bytes, header: SnapshotHeader) -> Path:
    stamp = "".join(character for character in header.created_at if character.isdigit())
    path = snapshots_dir() / f"{stamp[:8]}-{stamp[8:14]}-e{header.fleet_epoch}-r{header.revision}{SUFFIX}"
    write_private(path, blob)
    for stale in saved_snapshots()[KEEP:]:
        stale.unlink(missing_ok=True)
    return path


def saved_snapshots() -> list[Path]:
    """This context's snapshots, newest first (names sort by time)."""
    directory = snapshots_dir()
    if not directory.is_dir():
        return []
    return sorted((path for path in directory.iterdir() if path.name.endswith(SUFFIX)), reverse=True)


def newer_saved_snapshot(header: SnapshotHeader) -> Path | None:
    """This context's newest snapshot of the same fleet if it is newer than `header`, else None."""

    def order(item: SnapshotHeader) -> tuple[int, int, datetime]:
        return item.fleet_epoch, item.revision, datetime.fromisoformat(item.created_at.replace("Z", "+00:00"))

    for path in saved_snapshots():
        try:
            saved = read_header(path.read_bytes())
        except (OSError, SnapshotError):
            continue
        if saved.fleet_id == header.fleet_id:
            return path if order(saved) > order(header) else None
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
