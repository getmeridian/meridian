"""Turn a decrypted snapshot's database into the state a restored service starts from.

Runs on the deployer, before the file is uploaded: the fleet epoch goes up by one,
every node certificate is revoked, and a new fleet CA and signing key are created,
so each node document is signed again under the new epoch. Agents enroll again
(the reviewed apply sees the new `ca_sha256` in their bootstrap), and an agent that
has accepted the new epoch refuses anything the old server still signs.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from meridian.control.backups import NOT_SNAPSHOTTED
from meridian.control.errors import ServiceError
from meridian.control.models import DeployerState
from meridian.control.service import ControlService
from meridian.control.store import Store
from meridian.core.errors import MeridianError


class RestoreError(MeridianError):
    """A snapshot's database that cannot become a restored service's."""


@dataclass(frozen=True)
class StagedDatabase:
    fleet_epoch: int
    sha256: str


def read_deployer_state(database: bytes) -> DeployerState | None:
    """The deployer's fleet files a snapshot carries, if any device deposited them."""
    copy = sqlite3.connect(":memory:")
    try:
        copy.deserialize(database)
        row = copy.execute("SELECT body FROM backup_state WHERE name = 'deployer_state'").fetchone()
    except sqlite3.Error as exc:
        raise RestoreError(f"The snapshot's database is unreadable: {exc}", category="user") from exc
    finally:
        copy.close()
    return DeployerState.model_validate_json(row[0]) if row else None


def stage_restored_database(
    database: bytes, path: Path, *, fleet_id: str, version: str, clock: Callable[[], float] = time.time
) -> StagedDatabase:
    """Write the database to `path` with the next epoch and a new fleet CA; one file, no WAL beside it."""
    for stale in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        stale.unlink(missing_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.touch(mode=0o600)
    path.write_bytes(database)
    try:
        store = Store(path)
        try:
            if store.meta("fleet_id") != fleet_id:
                raise RestoreError("The snapshot belongs to another fleet.", category="user")
            with store.transaction():
                epoch = store.fleet_epoch + 1
                store.set_meta("fleet_epoch", str(epoch))
                for statement in NOT_SNAPSHOTTED:
                    store.db.execute(statement)
                store.db.execute(
                    "UPDATE node_certificates SET revoked_at = ? WHERE revoked_at IS NULL", (int(clock()),)
                )
        finally:
            store.close()
        # Mints the new CA and signing key, and signs every node document again under the new epoch.
        ControlService(path, fleet_id=fleet_id, version=version, clock=clock).close()
        single = sqlite3.connect(path)
        try:
            single.execute("PRAGMA journal_mode=DELETE")
        finally:
            single.close()
    except ServiceError as exc:
        raise RestoreError(
            f"The snapshot's database cannot be restored: {exc}",
            hint="A snapshot from a newer Meridian needs that version: run `meridian update`.",
            category="user",
        ) from exc
    except sqlite3.Error as exc:
        raise RestoreError(f"The snapshot's database is unreadable: {exc}", category="user") from exc
    return StagedDatabase(fleet_epoch=epoch, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
