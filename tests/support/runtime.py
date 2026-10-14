"""MockConnection stubs for the panel runtime-migration probe (``provision.runtime_migration``)."""

from __future__ import annotations

from tests.support.mock_connection import MockConnection

PROBE = "runtime-probe-done"


def migrated_panel(*facts: str, conn: MockConnection | None = None) -> MockConnection:
    """A connection whose migration probe reports ``facts`` (e.g. ``legacy-dir``); none means nothing to migrate."""
    conn = conn or MockConnection()
    conn.when(PROBE, stdout="".join(f"{fact}\n" for fact in (*facts, PROBE)))
    return conn
