"""Shared privacy defaults and storage; the privacy settings framework owns writes."""

from __future__ import annotations

import sqlite3

from meridian.control.models import PrivacySettings

PRIVACY_TABLES = (
    "CREATE TABLE IF NOT EXISTS privacy (id INTEGER PRIMARY KEY CHECK (id = 1), settings TEXT NOT NULL);",
    "CREATE TABLE IF NOT EXISTS privacy_changes (id INTEGER PRIMARY KEY AUTOINCREMENT, changed_at TEXT NOT NULL,\n"
    "    author TEXT NOT NULL, before TEXT NOT NULL, after TEXT NOT NULL);",
)


def read_privacy(db: sqlite3.Connection) -> PrivacySettings:
    """Read the singleton in an existing privacy table; an absent row means all defaults."""
    row = db.execute("SELECT settings FROM privacy WHERE id = 1").fetchone()
    return PrivacySettings.model_validate_json(row[0]) if row is not None else PrivacySettings()
