"""A real control service (`meridian serve`) on a loopback port, for adapter and command tests."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from meridian.control.enrollment import token_sha256
from meridian.control.models import PrivacySettings, ServiceConfig
from meridian.control.server import ControlServer
from meridian.control.service import ControlService
from meridian.control_artifacts import ServicePackage
from meridian.provision.control_service import ControlServiceInstall
from meridian.provision.steps import ProvisionContext

FLEET_ID = "0f1e2d3c-4b5a-4968-8776-655443322110"
ADMIN_TOKEN = "admin-token-for-tests-only-0123456789"


def write_privacy(database: Path, settings: PrivacySettings) -> None:
    """Set shared privacy settings directly; the other worker owns the production write API."""
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO privacy VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET settings = excluded.settings",
            (settings.model_dump_json(),),
        )


def drop_device_privacy_schema(db: sqlite3.Connection) -> None:
    """Remove device-era fields before a fixture rewinds to a schema from before privacy settings."""
    for table in ("devices", "privacy_changes", "privacy"):
        db.execute(f"DROP TABLE {table}")
    db.execute("ALTER TABLE users DROP COLUMN device_limit")


@contextmanager
def serve_control(state_dir: Path, *, public_path: str = "") -> Iterator[str]:
    """Run a control service whose state lives in `state_dir`; yields its base URL.

    `public_path` stands in for the secret path nginx strips; the web UI's sign-in needs one.
    """
    service = ControlService(state_dir / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    server = ControlServer(("127.0.0.1", 0), service, token_sha256(ADMIN_TOKEN), public_path=public_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def bootstrap_context(**fields: object) -> ProvisionContext:
    """A control bootstrap's provisioning context: the given fields plus the service install."""
    ctx = ProvisionContext(**{"ip": "198.51.100.1", **fields})  # type: ignore[arg-type]
    ctx.control_service = ControlServiceInstall(
        package=ServicePackage(requirement="meridian-vpn==4.0.0"),
        config=ServiceConfig(fleet_id=FLEET_ID, admin_token_sha256="a" * 64),
    )
    return ctx
