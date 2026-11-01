"""Bulk schema upgrades retain people identities while adding flow-block evidence."""

import sqlite3
from pathlib import Path

from meridian.control.service import SCHEMA_VERSION, ControlService
from tests.support.control_service import FLEET_ID, drop_flow_blocks_schema


def test_bulk_schema_twenty_upgrades_without_replaying_people_versions(tmp_path: Path):
    path = tmp_path / "bulk.db"
    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    user = service.put_user("alice")
    before = service._store.row("SELECT * FROM people_versions WHERE user_id = ?", (user.user_id,))
    service.close()
    with sqlite3.connect(path) as database:
        drop_flow_blocks_schema(database)
        database.execute("UPDATE meta SET value = ? WHERE key = ?", ("20", "schema_version"))
    restored = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    try:
        current = restored.get_user("alice")
        assert current.credential_generation == 1
        assert (current.user_id, current.uuid, current.sub_token) == (user.user_id, user.uuid, user.sub_token)
        after = restored._store.row("SELECT * FROM people_versions WHERE user_id = ?", (user.user_id,))
        assert tuple(after) == tuple(before)
        assert restored._store.meta("schema_version") == str(SCHEMA_VERSION)
        assert restored.user_flow_blocks("alice").nodes == []
    finally:
        restored.close()
