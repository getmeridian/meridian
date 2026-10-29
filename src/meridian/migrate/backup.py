"""Back up the panel before anything changes: a PostgreSQL dump, the panel's `.env`, and `cluster.yml`.

The dump stays on the control server under `SERVER_BACKUP_ROOT`, which teardown
never deletes, and a copy (checked by SHA-256) goes to the deployer's home with
`cluster.yml` and the user snapshot the import reads. There is no rollback
window: these files are how an operator restores Remnawave by hand.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import shlex
import shutil
from pathlib import Path

from meridian import config
from meridian.host_runtime import LEGACY_PANEL_DIR, PANEL_DATABASE, PANEL_DIR
from meridian.migrate import MigrationError
from meridian.ssh import ServerConnection

SERVER_BACKUP_ROOT = "/var/backups/meridian-migration"
DUMP_NAME = "panel.dump"
_DONE = "backup-done"


def local_backup_root() -> Path:
    return config.MERIDIAN_HOME / "backups"


def backup_panel(conn: ServerConnection, stamp: str) -> tuple[Path, str]:
    """Dump the panel on the control server and copy it home; return (local directory, server directory)."""
    remote = f"{SERVER_BACKUP_ROOT}/{stamp}"
    result = conn.run(_dump_command(remote), timeout=900, operation_name="back up the panel database")
    lines = result.stdout.split()
    if result.returncode != 0 or _DONE not in lines:
        detail = (result.stderr or result.stdout).strip()[-300:]
        raise MigrationError(
            f"Could not back up the panel database: {detail or f'exit {result.returncode}'}",
            hint="Check the panel on the control server (docker ps), then run `meridian migrate` again.",
        )
    digest = lines[lines.index(_DONE) - 1] if lines.index(_DONE) > 0 else ""
    local = local_backup_root() / f"migrate-{stamp}"
    local.mkdir(parents=True, exist_ok=True, mode=0o700)
    local.chmod(0o700)
    _fetch_dump(conn, f"{remote}/{DUMP_NAME}", local / DUMP_NAME, digest)
    for name in ("panel.env", "docker-compose.yml"):
        copied = conn.get_text(f"{remote}/{name}", timeout=60)
        if copied.returncode == 0:
            _write_private(local / name, copied.stdout.encode("utf-8"))
    if config.CLUSTER_CONFIG.exists():
        _write_private(local / "cluster.yml", config.CLUSTER_CONFIG.read_bytes())
    if config.SERVER_PROFILES_FILE.exists():
        _write_private(local / "servers.json", config.SERVER_PROFILES_FILE.read_bytes())
    return local, remote


def write_snapshot(local: Path, name: str, content: str) -> None:
    """Save one more private file (the user snapshot) beside the backup."""
    local.mkdir(parents=True, exist_ok=True, mode=0o700)
    _write_private(local / name, content.encode("utf-8"))


def _dump_command(remote: str) -> str:
    """Find the database container under either name and dump it in PostgreSQL's custom format."""
    directory = shlex.quote(remote)
    names = " ".join(shlex.quote(name) for name in (PANEL_DATABASE.name, PANEL_DATABASE.legacy))
    panel_dirs = " ".join(shlex.quote(path) for path in (PANEL_DIR, LEGACY_PANEL_DIR))
    return (
        f"umask 077; mkdir -p {directory} || exit 1; db=;"
        f' for c in {names}; do if docker inspect --type container "$c" >/dev/null 2>&1;'
        " then db=$c; break; fi; done;"
        ' [ -n "$db" ] || { echo "no panel database container" >&2; exit 3; };'
        # The container's own environment names the role and database (POSTGRES_USER/POSTGRES_DB).
        f' docker exec "$db" sh -c \'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc\''
        f" > {directory}/{DUMP_NAME}.part || exit 1;"
        f" mv {directory}/{DUMP_NAME}.part {directory}/{DUMP_NAME} || exit 1;"
        f' for d in {panel_dirs}; do if [ -f "$d/.env" ]; then'
        f' cp "$d/.env" {directory}/panel.env; cp "$d/docker-compose.yml" {directory}/ 2>/dev/null; break; fi; done;'
        f" sha256sum {directory}/{DUMP_NAME} | cut -d' ' -f1; echo {_DONE}"
    )


def _fetch_dump(conn: ServerConnection, remote: str, local: Path, digest: str) -> None:
    fetched = conn.run(f"base64 {shlex.quote(remote)}", timeout=900, sensitive=True)
    try:
        data = base64.b64decode("".join(fetched.stdout.split()), validate=True) if fetched.returncode == 0 else b""
    except binascii.Error:
        data = b""
    if not data or hashlib.sha256(data).hexdigest() != digest:
        raise MigrationError(
            "The panel backup did not arrive intact on this machine.",
            hint=f"It is still on the control server in {remote}. Run `meridian migrate` again.",
        )
    _write_private(local, data)


def _write_private(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.chmod(0o600)
    shutil.move(str(temporary), str(path))
