"""Operator-owned privacy settings, their audit trail, and atomic removal of higher-level data.

The device ledger owns its table. This ledger can lower device levels even on a
service whose schema does not yet have it. Private fingerprints share one fleet
secret; callers of `private_device_key` hold the store's transaction.
"""

from __future__ import annotations

import hmac
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from meridian.control.models import (
    PrivacyChange,
    PrivacyDeletedRows,
    PrivacySettings,
    PrivacyState,
    PrivacyUpdateResult,
)
from meridian.control.store import Store
from meridian.control.times import format_timestamp

PRIVACY_TABLE = "CREATE TABLE IF NOT EXISTS privacy (id INTEGER PRIMARY KEY CHECK (id = 1), settings TEXT NOT NULL);"
PRIVACY_CHANGES_TABLE = (
    "CREATE TABLE IF NOT EXISTS privacy_changes (id INTEGER PRIMARY KEY AUTOINCREMENT, changed_at TEXT NOT NULL,\n"
    "    author TEXT NOT NULL, before TEXT NOT NULL, after TEXT NOT NULL);"
)
PRIVACY_TABLES = (PRIVACY_TABLE, PRIVACY_CHANGES_TABLE)
DEVICE_HMAC_SECRET = "device_hmac_key"
HISTORY_TABLE_NAMES = ("traffic_daily", "node_traffic_daily")


def read_privacy(store: Store) -> PrivacySettings:
    """The one stored row, or privacy-first defaults when it is absent."""
    row = store.row("SELECT settings FROM privacy WHERE id = 1")
    return PrivacySettings.model_validate_json(row["settings"]) if row else PrivacySettings()


def private_device_key(store: Store, hwid: str) -> str:
    """HMAC-SHA256 of a device ID; create the unsnapshotted fleet key on first use, in a held transaction."""
    key = store.secret(DEVICE_HMAC_SECRET)
    if key is None:
        key = secrets.token_bytes(32)
        store.set_secret(DEVICE_HMAC_SECRET, key)
    return hmac.digest(key, hwid.encode("utf-8"), "sha256").hex()


def history_cutoff(days: int, now: str) -> str:
    """Oldest retained UTC day, including today as one of `days`."""
    today = datetime.fromisoformat(now).astimezone(UTC).date()
    return (today - timedelta(days=days - 1)).isoformat()


def prune_history_rows(store: Store, days: int, now: str) -> PrivacyDeletedRows:
    """Remove days outside retention in the caller's transaction; shared by changes and the timer."""
    cutoff = history_cutoff(days, now)
    counts = {
        table: store.db.execute(f"DELETE FROM {table} WHERE day < ?", (cutoff,)).rowcount
        for table in HISTORY_TABLE_NAMES
    }
    return PrivacyDeletedRows.model_validate(counts)


class PrivacyLedger:
    def __init__(self, store: Store, *, clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._clock = clock
        for table in PRIVACY_TABLES:
            store.create(table)

    def state(self) -> PrivacyState:
        with self._store.lock:
            rows = self._store.rows(
                "SELECT changed_at, author, before, after FROM privacy_changes ORDER BY id DESC LIMIT 50"
            )
            return PrivacyState(
                settings=read_privacy(self._store),
                changes=[
                    PrivacyChange(
                        changed_at=row["changed_at"],
                        author=row["author"],
                        before=PrivacySettings.model_validate_json(row["before"]),
                        after=PrivacySettings.model_validate_json(row["after"]),
                    )
                    for row in rows
                ],
            )

    def set(self, settings: PrivacySettings, *, author: str) -> PrivacyUpdateResult:
        """Change levels, remove or re-key their old state, and record who did it in one transaction."""
        with self._store.transaction():
            before = read_privacy(self._store)
            if before == settings:
                return PrivacyUpdateResult(**self.state().model_dump(), changed=False)
            now = format_timestamp(self._clock())
            device_deleted = user_deleted = node_deleted = 0
            rekeyed = 0
            if self._store.row("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'devices'"):
                if settings.device_limits == "off" or (before.device_limits, settings.device_limits) == (
                    "private",
                    "full",
                ):
                    # Fingerprints cannot be reversed into IDs: full mode starts with registrations on the next fetch.
                    device_deleted = self._store.db.execute("DELETE FROM devices").rowcount
                elif (before.device_limits, settings.device_limits) == ("full", "private"):
                    rekeyed = self._rekey_devices()
            if settings.usage_history == "off":
                user_deleted = self._store.db.execute("DELETE FROM traffic_daily").rowcount
                node_deleted = self._store.db.execute("DELETE FROM node_traffic_daily").rowcount
            elif settings.history_days < before.history_days:
                pruned = prune_history_rows(self._store, settings.history_days, now)
                user_deleted, node_deleted = pruned.traffic_daily, pruned.node_traffic_daily
            self._store.db.execute(
                "INSERT INTO privacy VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET settings = excluded.settings",
                (settings.model_dump_json(),),
            )
            self._store.db.execute(
                "INSERT INTO privacy_changes (changed_at, author, before, after) VALUES (?, ?, ?, ?)",
                (now, author, before.model_dump_json(), settings.model_dump_json()),
            )
            return PrivacyUpdateResult(
                **self.state().model_dump(),
                changed=True,
                deleted=PrivacyDeletedRows(
                    devices=device_deleted, traffic_daily=user_deleted, node_traffic_daily=node_deleted
                ),
                rekeyed_devices=rekeyed,
            )

    def _rekey_devices(self) -> int:
        rows = self._store.rows("SELECT user_id, device_key, first_seen_at, last_seen_at FROM devices")
        # Remove then insert, so a raw ID equal to another row's future hash never collides mid-conversion.
        converted = [
            (
                row["user_id"],
                private_device_key(self._store, row["device_key"]),
                row["first_seen_at"],
                row["last_seen_at"],
            )
            for row in rows
        ]
        self._store.db.execute("DELETE FROM devices")
        self._store.db.executemany(
            "INSERT INTO devices (user_id, device_key, platform, os_version, model, user_agent,"
            " first_seen_at, last_seen_at)"
            " VALUES (?, ?, NULL, NULL, NULL, NULL, ?, ?)",
            converted,
        )
        return len(rows)
