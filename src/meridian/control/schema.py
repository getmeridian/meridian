"""Forward-only SQLite schema steps for the control service."""

from __future__ import annotations

from meridian.control.backups import BACKUP_DEVICES_TABLE, BACKUP_STATE_TABLE
from meridian.control.commands import COMMAND_TABLES
from meridian.control.devices import DEVICES_TABLE
from meridian.control.errors import ServiceError
from meridian.control.events import EVENT_TABLES
from meridian.control.owner import UI_TABLES
from meridian.control.period_usage import DIRECTIONAL_PERIOD_MIGRATION
from meridian.control.privacy import PRIVACY_TABLES
from meridian.control.revisions import APPLY_FENCE_TABLE, REVISIONS_TABLE
from meridian.control.server_allowances import ALLOWANCE_PERIODS_TABLE
from meridian.control.store import Store
from meridian.control.tokens import API_TOKENS_TABLE_V4
from meridian.control.traffic import ACCOUNTING_MIGRATION, DAILY_TABLES, PER_EPOCH_BASELINES

SCHEMA_VERSION = 18
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS secrets (name TEXT PRIMARY KEY, value BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY,
    spec TEXT,
    spec_sha256 TEXT,
    revision INTEGER,
    document TEXT,
    doc_sha256 TEXT,
    applied_revision INTEGER,
    applied_sha256 TEXT,
    last_seen_at TEXT,
    agent_version TEXT,
    report TEXT
);
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    user_id TEXT NOT NULL UNIQUE,
    uuid TEXT NOT NULL UNIQUE,
    sub_token TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expire_at TEXT,
    traffic_limit_bytes INTEGER NOT NULL DEFAULT 0,
    traffic_limit_strategy TEXT NOT NULL DEFAULT 'no_reset',
    traffic_offset_bytes INTEGER NOT NULL DEFAULT 0,
    traffic_upload_offset_bytes INTEGER NOT NULL DEFAULT 0,
    traffic_download_offset_bytes INTEGER NOT NULL DEFAULT 0,
    traffic_period_carried_bytes INTEGER NOT NULL DEFAULT 0,
    traffic_reset_at TEXT,
    traffic_carried_bytes INTEGER NOT NULL DEFAULT 0,
    device_limit INTEGER,
    note TEXT NOT NULL DEFAULT '',
    tag TEXT NOT NULL DEFAULT '',
    language TEXT
);
CREATE TABLE IF NOT EXISTS endpoints (endpoint_id TEXT PRIMARY KEY, body TEXT NOT NULL);
"""
SCHEMA += "\n".join(PRIVACY_TABLES) + "\n" + DEVICES_TABLE + ";\n"
SCHEMA += ";\n".join(COMMAND_TABLES) + ";\n"
SCHEMA += ALLOWANCE_PERIODS_TABLE + ";\n"
# Statements that bring a database from version N to N + 1 (index N - 1).
_MIGRATIONS: tuple[tuple[str, ...], ...] = (
    (
        "ALTER TABLE users ADD COLUMN expire_at TEXT",
        "ALTER TABLE users ADD COLUMN traffic_limit_bytes INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN traffic_limit_strategy TEXT NOT NULL DEFAULT 'no_reset'",
    ),
    (
        "ALTER TABLE users ADD COLUMN traffic_offset_bytes INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN traffic_reset_at TEXT",
    ),
    (API_TOKENS_TABLE_V4,),
    (REVISIONS_TABLE, APPLY_FENCE_TABLE),
    ("ALTER TABLE users ADD COLUMN traffic_carried_bytes INTEGER NOT NULL DEFAULT 0",),
    (BACKUP_DEVICES_TABLE, BACKUP_STATE_TABLE),
    # 7 -> 8: the web UI's owner, sessions, and setup links (`owner.py`).
    UI_TABLES,
    PER_EPOCH_BASELINES,
    # 9 -> 10: operator-owned privacy settings and optional UTC daily usage.
    (*PRIVACY_TABLES, *DAILY_TABLES),
    # 10 -> 11: subscription devices and operator-owned per-user caps.
    (DEVICES_TABLE, "ALTER TABLE users ADD COLUMN device_limit INTEGER"),
    # 11 -> 12: optional expiry of scoped API tokens; existing tokens never expire.
    ("ALTER TABLE api_tokens ADD COLUMN expires_at TEXT",),
    # 12 -> 13: durable accounting, aggregate activity, and local lifecycle events.
    (*ACCOUNTING_MIGRATION, *EVENT_TABLES),
    # 13 -> 14: signed node commands and the capabilities accepted by each session.
    COMMAND_TABLES,
    # 14 -> 15: private, operator-owned person notes and tags.
    (
        "ALTER TABLE users ADD COLUMN note TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE users ADD COLUMN tag TEXT NOT NULL DEFAULT ''",
    ),
    # 15 -> 16: server allowance offsets over durable accepted node totals.
    (ALLOWANCE_PERIODS_TABLE,),
    # 16 -> 17: optional operator-owned subscription language per person.
    ("ALTER TABLE users ADD COLUMN language TEXT",),
    DIRECTIONAL_PERIOD_MIGRATION,
)


def migrate(store: Store) -> None:
    stored = int(store.meta("schema_version") or SCHEMA_VERSION)
    if stored > SCHEMA_VERSION:
        raise ServiceError("conflict", "The database was written by a newer Meridian.", 409)
    for statements in _MIGRATIONS[stored - 1 : SCHEMA_VERSION - 1]:
        for statement in statements:
            store.db.execute(statement)
    store.set_meta("schema_version", str(SCHEMA_VERSION))
