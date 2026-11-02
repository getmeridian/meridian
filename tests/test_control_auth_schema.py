"""Owner credentials stay local while issued token grants survive schema upgrades."""

import json
import sqlite3
from pathlib import Path

from meridian.control.backups import strip_unsnapshotted
from meridian.control.models import TokenCreate
from meridian.control.service import SCHEMA_VERSION, ControlService
from meridian.control.token_capabilities import legacy_capabilities
from meridian.control.tokens import ROOT
from tests.support.control_service import FLEET_ID, drop_owner_capability_schema


def test_schema_twenty_one_preserves_tokens_and_adds_owner_passkey_storage(tmp_path: Path) -> None:
    database = tmp_path / "control.db"
    service = ControlService(database, fleet_id=FLEET_ID, version="4.0.0")
    issued = service.tokens.issue(TokenCreate(name="Integration", scope="people"), issuer=ROOT)
    with sqlite3.connect(database) as db:
        drop_owner_capability_schema(db)
        db.execute("UPDATE meta SET value = '21' WHERE key = 'schema_version'")

    upgraded = ControlService(database, fleet_id=FLEET_ID, version="4.0.0")
    assert upgraded.tokens.authenticate(issued.token).scope == "people"
    with sqlite3.connect(database) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"ui_passkeys", "ui_passkey_owner"} <= tables
        stored = db.execute("SELECT capabilities FROM api_tokens WHERE token_id = ?", (issued.id,)).fetchone()[0]
        assert json.loads(stored) == sorted(legacy_capabilities("people"))
        assert db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == (str(SCHEMA_VERSION),)


def test_snapshots_strip_passkeys_and_owner_handles_from_any_schema(tmp_path: Path) -> None:
    with sqlite3.connect(tmp_path / "snapshot.db") as db:
        db.execute("CREATE TABLE ui_passkeys (credential_id TEXT)")
        db.execute("CREATE TABLE ui_passkey_owner (user_handle BLOB)")
        db.execute("INSERT INTO ui_passkeys VALUES ('owner-credential')")
        db.execute("INSERT INTO ui_passkey_owner VALUES (X'00112233')")
        strip_unsnapshotted(db)
        assert db.execute("SELECT COUNT(*) FROM ui_passkeys").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM ui_passkey_owner").fetchone() == (0,)
