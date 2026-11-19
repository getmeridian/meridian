"""Track recoverable owner changes, not traffic, session touches, or requests that wrote nothing.

These are temporary SQLite triggers, not part of a snapshot. A changed durable value
counts once per transaction including bulk edits and cascading updates. Failed writes
roll the counter back with the underlying changes.
"""

from meridian.control.store import Store

BACKUP_PROGRESS_TABLE = """CREATE TABLE IF NOT EXISTS backup_progress (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    changes INTEGER NOT NULL DEFAULT 0
)"""
BACKUP_RECEIPTS_TABLE = """CREATE TABLE IF NOT EXISTS backup_receipts (
    device_id TEXT PRIMARY KEY REFERENCES backup_devices(device_id),
    changes INTEGER NOT NULL,
    saved_at TEXT NOT NULL,
    captured INTEGER NOT NULL
)"""
BACKUP_EXPORTS_TABLE = """CREATE TABLE IF NOT EXISTS backup_exports (
    id INTEGER PRIMARY KEY, exported_at TEXT NOT NULL, actor TEXT NOT NULL
)"""
BACKUP_SCHEMA = (
    BACKUP_PROGRESS_TABLE,
    BACKUP_RECEIPTS_TABLE,
    BACKUP_EXPORTS_TABLE,
    "INSERT OR IGNORE INTO backup_progress(id) VALUES (1)",
)

# All columns matter in the small owner-owned tables. For tables shared with
# background data, name the owner state: a new hello or token read is not an edit.
_WATCH: dict[str, tuple[str, ...] | None] = {
    "users": None,
    "meta": None,
    "privacy": None,
    "user_access_memberships": None,
    "person_delivery_policies": None,
    "endpoints": None,
    "devices": ("device_key",),
    "apply_fence": ("record",),
    "nodes": ("spec",),
    "api_tokens": ("name", "scope", "token_sha256", "expires_at", "capabilities", "revoked_at"),
    "revisions": None,
    "backup_devices": None,
    "backup_state": None,
}


def track_backup_changes(store: Store) -> None:
    """Install after migrations and all ledgers have created their tables."""
    counted = -1

    def first_change() -> int:
        nonlocal counted
        if store.db.in_transaction and counted == store.transaction_number:
            return 0
        counted = store.transaction_number
        return 1

    store.db.create_function("first_backup_change", 0, first_change)
    for table, selected in _WATCH.items():
        columns = selected or tuple(str(row[1]) for row in store.db.execute(f"PRAGMA table_info({table})"))
        if not columns:
            raise RuntimeError(f"Missing backup-tracked table {table}")
        changed = " OR ".join(f"NEW.{col} IS NOT OLD.{col}" for col in columns)
        if table == "apply_fence":
            changed = " OR ".join(
                f"json_extract(NEW.record, '$.{key}') IS NOT json_extract(OLD.record, '$.{key}')"
                for key in ("fleet_id", "generation", "revision", "plan_hash")
            )
        for action, when in (("INSERT", "1"), ("DELETE", "1"), ("UPDATE", f"({changed})")):
            if table == "devices" and action != "DELETE":
                continue  # ordinary subscription fetches register/touch slots; owner deletions matter
            if table == "nodes" and action != "UPDATE":
                when = ("NEW" if action == "INSERT" else "OLD") + ".spec IS NOT NULL"
            # Temp triggers disappear on close and never reach a standalone restore database.
            store.db.execute(
                f"CREATE TEMP TRIGGER backup_{table}_{action} AFTER {action} ON main.{table} "
                f"WHEN {when} BEGIN UPDATE backup_progress SET changes=changes+1 WHERE first_backup_change(); END"
            )
