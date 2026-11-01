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
from meridian.core.account_schedule import AccountLifecycleState
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
    drop_operations_schema(db)
    drop_token_expiry_schema(db)


def drop_token_expiry_schema(db: sqlite3.Connection) -> None:
    """An older-schema fixture must not retain the column step 12 adds."""
    if "expires_at" in {column[1] for column in db.execute("PRAGMA table_info(api_tokens)")}:
        db.execute("ALTER TABLE api_tokens DROP COLUMN expires_at")
    drop_person_metadata_schema(db)


def drop_person_metadata_schema(db: sqlite3.Connection) -> None:
    """Older fixtures must not retain the operator metadata added in schema step 15."""
    drop_people_query_schema(db)
    columns = {column[1] for column in db.execute("PRAGMA table_info(users)")}
    for column in ("note", "tag", "language", "credential_generation"):
        if column in columns:
            db.execute(f"ALTER TABLE users DROP COLUMN {column}")
    drop_period_usage_schema(db)


def drop_period_usage_schema(db: sqlite3.Connection) -> None:
    """Remove directional baselines before rewinding to a combined-only period schema."""
    drop_people_query_schema(db)
    columns = {column[1] for column in db.execute("PRAGMA table_info(users)")}
    for column in ("traffic_upload_offset_bytes", "traffic_download_offset_bytes", "traffic_period_carried_bytes"):
        if column in columns:
            db.execute(f"ALTER TABLE users DROP COLUMN {column}")
    drop_account_schedule_schema(db)


def drop_account_schedule_schema(db: sqlite3.Connection) -> None:
    """Rewind schema step 19 without retaining its account schedule columns."""
    drop_people_query_schema(db)
    columns = {column[1] for column in db.execute("PRAGMA table_info(users)")}
    for column in AccountLifecycleState.model_fields:
        if column in columns:
            db.execute(f"ALTER TABLE users DROP COLUMN {column}")
    drop_flow_blocks_schema(db)


def drop_flow_blocks_schema(db: sqlite3.Connection) -> None:
    """Rewind the generation and flow-block receipts introduced in schema step 21."""
    columns = {column[1] for column in db.execute("PRAGMA table_info(users)")}
    if "credential_generation" in columns:
        db.execute("ALTER TABLE users DROP COLUMN credential_generation")
    db.execute("DROP TABLE IF EXISTS user_flow_blocks")
    columns = {column[1] for column in db.execute("PRAGMA table_info(nodes)")}
    for column in ("agent_capabilities", "report_seen_at"):
        if column in columns:
            db.execute(f"ALTER TABLE nodes DROP COLUMN {column}")


def drop_operations_schema(db: sqlite3.Connection) -> None:
    """Rewind step 13 without leaving newer columns in older fixtures."""
    for table in ("node_traffic_totals", "events", "event_node_states"):
        db.execute(f"DROP TABLE IF EXISTS {table}")
    columns = {row[1] for row in db.execute("PRAGMA table_info(traffic_totals)")}
    for column in ("first_traffic_at", "last_traffic_node"):
        if column in columns:
            db.execute(f"ALTER TABLE traffic_totals DROP COLUMN {column}")
    drop_person_metadata_schema(db)


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


def drop_people_query_schema(db: sqlite3.Connection) -> None:
    """Rewound fixtures drop later people indices, triggers, and receipt tables."""
    for kind, name in db.execute(
        "SELECT type, name FROM sqlite_master WHERE name LIKE 'people_%' AND type IN ('index', 'trigger')"
    ).fetchall():
        db.execute(f"DROP {kind.upper()} {name}")
    db.execute("DROP TABLE IF EXISTS bulk_receipts")
    db.execute("DROP TABLE IF EXISTS people_versions")
    drop_flow_blocks_schema(db)
