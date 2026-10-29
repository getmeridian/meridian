"""The control service's one SQLite file and its process-wide lock.

`ControlService`, `NodeAuthority`, `TrafficLedger`, `TokenRegistry`, and
`RevisionLedger` each own their tables
and share this connection, lock, and change condition, so a transaction can
span them and a waiting poll wakes on any revision.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class Store:
    def __init__(self, database: Path) -> None:
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        database.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(database, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def create(self, schema: str) -> None:
        """Create tables; `executescript()` commits on its own, so never call it inside `transaction()`."""
        with self.lock:
            self.db.executescript(schema)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self.db.execute("ROLLBACK")
                raise
            self.db.execute("COMMIT")

    def row(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self.lock:
            cursor = self.db.cursor()
            cursor.row_factory = sqlite3.Row  # type: ignore[assignment]  # typeshed omits Row as a factory
            row: sqlite3.Row | None = cursor.execute(sql, params).fetchone()
            return row

    def rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self.lock:
            cursor = self.db.cursor()
            cursor.row_factory = sqlite3.Row  # type: ignore[assignment]
            return list(cursor.execute(sql, params).fetchall())

    def meta(self, key: str) -> str | None:
        with self.lock:
            row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return str(row[0]) if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value)
        )

    def secret(self, name: str) -> bytes | None:
        with self.lock:
            row = self.db.execute("SELECT value FROM secrets WHERE name = ?", (name,)).fetchone()
        return bytes(row[0]) if row else None

    def set_secret(self, name: str, value: bytes) -> None:
        self.db.execute(
            "INSERT INTO secrets VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET value = excluded.value", (name, value)
        )

    @property
    def fleet_epoch(self) -> int:
        return int(self.meta("fleet_epoch") or "1")
