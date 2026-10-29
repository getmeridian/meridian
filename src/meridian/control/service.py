"""The control service's state and logic, independent of HTTP.

One SQLite file holds the fleet identity, the signing key, node specs and the
signed documents composed from them, user records, and client endpoints. Every
change that alters a node's resources composes a new revision, signs it, and
wakes that node's waiting poll.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
import uuid as uuid_module
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from meridian.control.formats.entries import SubscriptionUser
from meridian.control.formats.selector import SUFFIX_FORMATS, select_format
from meridian.control.models import (
    ClientEndpoint,
    ManagedInbound,
    MintedToken,
    NodeSpec,
    NodeStatus,
    ServiceHealth,
    ServiceSettings,
    UserRecord,
    UserUpdate,
    users_resource_id,
)
from meridian.control.subscriptions import SubscriptionResponse, render_subscription, unknown_subscription
from meridian.core.agent_desired import DesiredNodeState
from meridian.core.agent_protocol import ObservedStateReport, PollRequest
from meridian.core.agent_signing import DESIRED_STATE_CONTEXT, generate_signing_key, public_key, sign
from meridian.core.agent_types import AGENT_PROTOCOL, NodeStateRef, canonical_sha256

SCHEMA_VERSION = 2
MAX_WAIT_SECONDS = 55
_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS secrets (name TEXT PRIMARY KEY, value BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY,
    token_sha256 TEXT,
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
    traffic_limit_strategy TEXT NOT NULL DEFAULT 'no_reset'
);
CREATE TABLE IF NOT EXISTS endpoints (endpoint_id TEXT PRIMARY KEY, body TEXT NOT NULL);
"""
# Statements that bring a database from version N to N + 1 (index N - 1).
_MIGRATIONS: tuple[tuple[str, ...], ...] = (
    (
        "ALTER TABLE users ADD COLUMN expire_at TEXT",
        "ALTER TABLE users ADD COLUMN traffic_limit_bytes INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN traffic_limit_strategy TEXT NOT NULL DEFAULT 'no_reset'",
    ),
)
_USER_COLUMNS = (
    "username, user_id, uuid, sub_token, status, created_at, expire_at, traffic_limit_bytes, traffic_limit_strategy"
)


class ServiceError(Exception):
    """A refused request: `code` is an agent error code, `status` its HTTP status."""

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def token_sha256(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ControlService:
    """Thread-safe service state over one SQLite database."""

    def __init__(self, database: Path, *, fleet_id: str, version: str) -> None:
        self._version = version
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        database.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(database, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(_SCHEMA)
        with self._transaction():
            stored = self._meta("fleet_id")
            if stored and stored != fleet_id:
                raise ServiceError("conflict", "The database belongs to another fleet.", 409)
            self._set_meta("fleet_id", fleet_id)
            self._migrate()
            if not self._meta("fleet_epoch"):
                self._set_meta("fleet_epoch", "1")
            if not self._meta("profile_title"):
                self._set_meta("profile_title", "Meridian")
            if self._secret("signing_key") is None:
                self._db.execute("INSERT INTO secrets VALUES ('signing_key', ?)", (generate_signing_key(),))
        self.fleet_id = fleet_id

    # -- identity -----------------------------------------------------------------

    @property
    def fleet_epoch(self) -> int:
        return int(self._meta("fleet_epoch") or "1")

    def public_key(self) -> bytes:
        return public_key(self._signing_key())

    def write_public_key(self, path: Path) -> None:
        """Publish the raw public key in base64 for SSH bootstrap to pin."""
        path.write_text(base64.b64encode(self.public_key()).decode("ascii") + "\n", encoding="ascii")
        path.chmod(0o644)

    def health(self) -> ServiceHealth:
        return ServiceHealth(version=self._version, fleet_id=self.fleet_id, fleet_epoch=self.fleet_epoch)

    def put_settings(self, settings: ServiceSettings) -> ServiceSettings:
        with self._transaction():
            self._set_meta("profile_title", settings.profile_title)
            self._set_meta("public_url", settings.public_url)
        return settings

    def settings(self) -> ServiceSettings:
        return ServiceSettings(
            profile_title=self._meta("profile_title") or "Meridian", public_url=self._meta("public_url") or ""
        )

    # -- nodes ----------------------------------------------------------------------

    def mint_token(self, node_id: str) -> MintedToken:
        """Issue a fresh node token; the previous one stops working."""
        token = secrets.token_urlsafe(32)
        with self._transaction():
            self._ensure_node(node_id)
            self._db.execute("UPDATE nodes SET token_sha256 = ? WHERE node_id = ?", (token_sha256(token), node_id))
        return MintedToken(node_id=node_id, node_token=token)

    def put_node_spec(self, node_id: str, spec: NodeSpec) -> NodeStatus:
        body = spec.model_dump(mode="json")
        with self._transaction():
            self._ensure_node(node_id)
            self._db.execute(
                "UPDATE nodes SET spec = ?, spec_sha256 = ? WHERE node_id = ?",
                (json.dumps(body), canonical_sha256(body), node_id),
            )
            self._compose(node_id)
        return self.node_status(node_id)

    def delete_node(self, node_id: str) -> None:
        with self._transaction():
            self._db.execute("DELETE FROM nodes WHERE node_id = ?", (node_id,))

    def node_status(self, node_id: str) -> NodeStatus:
        row = self._node(node_id)
        if row is None:
            raise ServiceError("not_found", f"Node {node_id!r} is unknown.", 404)
        applied = (
            NodeStateRef(fleet_epoch=self.fleet_epoch, revision=row["applied_revision"], sha256=row["applied_sha256"])
            if row["applied_revision"]
            else None
        )
        failed: list[str] = []
        errors: list[str] = []
        if row["report"]:
            for item in json.loads(row["report"]).get("resources", []):
                if item.get("status") == "failed":
                    failed.append(item["id"])
                    errors.append(f"{item['id']}: {(item.get('error') or {}).get('message', '')}")
        return NodeStatus(
            node_id=node_id,
            fleet_epoch=self.fleet_epoch,
            revision=row["revision"],
            sha256=row["doc_sha256"],
            spec_sha256=row["spec_sha256"],
            applied=applied,
            last_seen_at=row["last_seen_at"],
            agent_version=row["agent_version"],
            failed=failed,
            errors=errors,
        )

    def authenticate_node(self, token: str) -> str:
        digest = token_sha256(token)
        with self._lock:
            rows = self._db.execute("SELECT node_id, token_sha256 FROM nodes WHERE token_sha256 IS NOT NULL").fetchall()
        for node_id, stored in rows:
            if hmac.compare_digest(stored, digest):
                return str(node_id)
        raise ServiceError("unauthenticated", "Unknown node token.", 401)

    def poll(self, node_id: str, request: PollRequest) -> dict[str, Any]:
        """Wait up to `wait_seconds` for a document newer than `have`; return the poll response."""
        deadline = time.monotonic() + min(request.wait_seconds, MAX_WAIT_SECONDS)
        with self._changed:
            self._touch(node_id)
            while True:
                row = self._node(node_id)
                document = row["document"] if row is not None else None
                if row is not None and document and _differs(request.have, self.fleet_epoch, row):
                    desired: Any = json.loads(document)
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    desired = None
                    break
                self._changed.wait(remaining)
        return {
            "type": "poll_result",
            "fleet_epoch": self.fleet_epoch,
            "desired": desired,
            "commands": [],
            "poll_after_seconds": 0,
        }

    def report(self, node_id: str, report: ObservedStateReport, raw: dict[str, Any]) -> dict[str, Any]:
        if report.node_id != node_id or report.fleet_id != self.fleet_id:
            raise ServiceError("invalid_request", "The report names another node or fleet.", 400)
        applied = report.applied
        with self._transaction():
            self._db.execute(
                "UPDATE nodes SET applied_revision = ?, applied_sha256 = ?, last_seen_at = ?, agent_version = ?,"
                " report = ? WHERE node_id = ?",
                (
                    applied.revision if applied else None,
                    applied.sha256 if applied else None,
                    utc_now(),
                    report.versions.agent,
                    json.dumps(raw),
                    node_id,
                ),
            )
        return {"type": "report_ack", "applied_revision": applied.revision if applied else None}

    # -- users and endpoints -----------------------------------------------------------

    def put_user(self, username: str) -> UserRecord:
        """Create the user if missing (fresh credentials); an existing user keeps them."""
        with self._transaction():
            existing = self.get_user(username)
            if existing is not None:
                return existing
            self._db.execute(
                "INSERT INTO users (username, user_id, uuid, sub_token, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    username,
                    f"u{secrets.token_hex(8)}",
                    str(uuid_module.uuid4()),
                    secrets.token_urlsafe(18),
                    "active",
                    utc_now(),
                ),
            )
            self._compose_all()
            return self._require_user(username)

    def update_user(self, username: str, update: UserUpdate) -> UserRecord:
        """Apply the operator's change; extending an expired user's expiry makes it active again."""
        with self._transaction():
            user = self._require_user(username)
            changes = update.model_dump(include=update.model_fields_set)
            if "status" in changes and changes["status"] is None:
                del changes["status"]
            for field in ("traffic_limit_bytes", "traffic_limit_strategy"):
                if field in changes and changes[field] is None:
                    del changes[field]
            merged = user.model_copy(update=changes)
            if merged.status == "expired" and "expire_at" in changes and not _expired(merged, utc_now()):
                merged = merged.model_copy(update={"status": "active"})
            self._db.execute(
                "UPDATE users SET status = ?, expire_at = ?, traffic_limit_bytes = ?, traffic_limit_strategy = ? "
                "WHERE username = ?",
                (
                    merged.status,
                    merged.expire_at,
                    merged.traffic_limit_bytes,
                    merged.traffic_limit_strategy,
                    username,
                ),
            )
            self._enforce(utc_now())
            self._compose_all()
            return self._require_user(username)

    def reissue_user(self, username: str) -> UserRecord:
        """Replace the user's VLESS UUID and subscription token; old links stop working."""
        with self._transaction():
            self._require_user(username)
            self._db.execute(
                "UPDATE users SET uuid = ?, sub_token = ? WHERE username = ?",
                (str(uuid_module.uuid4()), secrets.token_urlsafe(18), username),
            )
            self._compose_all()
            return self._require_user(username)

    def enforce(self, now: str | None = None) -> list[str]:
        """Expire active users whose `expire_at` passed; return who changed."""
        with self._transaction():
            changed = self._enforce(now or utc_now())
            if changed:
                self._compose_all()
            return changed

    def get_user(self, username: str) -> UserRecord | None:
        with self._lock:
            row = self._db.execute(f"SELECT {_USER_COLUMNS} FROM users WHERE username = ?", (username,)).fetchone()
        return _user(row) if row else None

    def list_users(self) -> list[UserRecord]:
        with self._lock:
            rows = self._db.execute(f"SELECT {_USER_COLUMNS} FROM users ORDER BY username").fetchall()
        return [_user(row) for row in rows]

    def delete_user(self, username: str) -> None:
        with self._transaction():
            self._db.execute("DELETE FROM users WHERE username = ?", (username,))
            self._compose_all()

    def _require_user(self, username: str) -> UserRecord:
        user = self.get_user(username)
        if user is None:
            raise ServiceError("not_found", f"Unknown user {username!r}.", 404)
        return user

    def _enforce(self, now: str) -> list[str]:
        expired = [user.username for user in self.list_users() if user.status == "active" and _expired(user, now)]
        for username in expired:
            self._db.execute("UPDATE users SET status = 'expired' WHERE username = ?", (username,))
        return expired

    def put_endpoint(self, endpoint_id: str, endpoint: ClientEndpoint) -> ClientEndpoint:
        with self._transaction():
            self._db.execute(
                "INSERT INTO endpoints VALUES (?, ?) ON CONFLICT(endpoint_id) DO UPDATE SET body = excluded.body",
                (endpoint_id, endpoint.model_dump_json()),
            )
        return endpoint

    def get_endpoint(self, endpoint_id: str) -> ClientEndpoint | None:
        with self._lock:
            row = self._db.execute("SELECT body FROM endpoints WHERE endpoint_id = ?", (endpoint_id,)).fetchone()
        return ClientEndpoint.model_validate_json(row[0]) if row else None

    def delete_endpoint(self, endpoint_id: str) -> None:
        with self._transaction():
            self._db.execute("DELETE FROM endpoints WHERE endpoint_id = ?", (endpoint_id,))

    def subscription(
        self, sub_token: str, suffix: str = "", *, user_agent: str | None = None, accept: str | None = None
    ) -> SubscriptionResponse:
        """Answer a subscription request: the document a suffix names, else the one the app reads."""
        with self._lock:
            row = self._db.execute(f"SELECT {_USER_COLUMNS} FROM users WHERE sub_token = ?", (sub_token,)).fetchone()
            bodies = [item[0] for item in self._db.execute("SELECT body FROM endpoints").fetchall()]
            settings = self.settings()
        if row is None:
            return unknown_subscription()
        record = _user(row)
        user = SubscriptionUser(
            username=record.username,
            uuid=record.uuid,
            short_id=record.sub_token,
            status=record.status,
            expires_at=datetime.fromisoformat(record.expire_at) if record.expire_at else None,
            limit_bytes=record.traffic_limit_bytes,
        )
        endpoints = [ClientEndpoint.model_validate_json(body) for body in bodies]
        document = SUFFIX_FORMATS[suffix] if suffix else select_format(user_agent, accept)
        return render_subscription(
            user, endpoints, document, profile_title=settings.profile_title, public_url=settings.public_url
        )

    # -- composition -------------------------------------------------------------------

    def _compose_all(self) -> None:
        for (node_id,) in self._db.execute("SELECT node_id FROM nodes WHERE spec IS NOT NULL").fetchall():
            self._compose(str(node_id))

    def _compose(self, node_id: str) -> None:
        """Sign a new revision when the node's resources changed, and wake its poll."""
        row = self._node(node_id)
        if row is None or not row["spec"]:
            return
        spec = NodeSpec.model_validate_json(row["spec"])
        resources = _with_users(spec, self.list_users())
        resources_sha = canonical_sha256(resources)
        if resources_sha == row["doc_sha256"]:
            return
        revision = (row["revision"] or 0) + 1
        document: dict[str, Any] = {
            "protocol": AGENT_PROTOCOL,
            "fleet_id": self.fleet_id,
            "node_id": node_id,
            "fleet_epoch": self.fleet_epoch,
            "revision": revision,
            "sha256": resources_sha,
            "issued_at": utc_now(),
            "resources": resources,
        }
        document["signature"] = sign(document, DESIRED_STATE_CONTEXT, self._signing_key())
        DesiredNodeState.model_validate_json(json.dumps(document))
        self._db.execute(
            "UPDATE nodes SET revision = ?, document = ?, doc_sha256 = ? WHERE node_id = ?",
            (revision, json.dumps(document, ensure_ascii=False), resources_sha, node_id),
        )
        self._changed.notify_all()

    # -- storage helpers -----------------------------------------------------------------

    def _migrate(self) -> None:
        stored = int(self._meta("schema_version") or SCHEMA_VERSION)
        if stored > SCHEMA_VERSION:
            raise ServiceError("conflict", "The database was written by a newer Meridian.", 409)
        for statements in _MIGRATIONS[stored - 1 : SCHEMA_VERSION - 1]:
            for statement in statements:
                self._db.execute(statement)
        self._set_meta("schema_version", str(SCHEMA_VERSION))

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    def _meta(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return str(row[0]) if row else None

    def _set_meta(self, key: str, value: str) -> None:
        self._db.execute(
            "INSERT INTO meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value)
        )

    def _secret(self, name: str) -> bytes | None:
        row = self._db.execute("SELECT value FROM secrets WHERE name = ?", (name,)).fetchone()
        return bytes(row[0]) if row else None

    def _signing_key(self) -> bytes:
        with self._lock:
            key = self._secret("signing_key")
        if key is None:
            raise ServiceError("internal", "The signing key is missing.", 500)
        return key

    def _ensure_node(self, node_id: str) -> None:
        self._db.execute("INSERT OR IGNORE INTO nodes (node_id) VALUES (?)", (node_id,))

    def _touch(self, node_id: str) -> None:
        self._db.execute("UPDATE nodes SET last_seen_at = ? WHERE node_id = ?", (utc_now(), node_id))

    def _node(self, node_id: str) -> sqlite3.Row | None:
        cursor = self._db.cursor()
        cursor.row_factory = sqlite3.Row  # type: ignore[assignment]  # typeshed omits Row as a factory
        row: sqlite3.Row | None = cursor.execute("SELECT * FROM nodes WHERE node_id = ?", (node_id,)).fetchone()
        return row


def _differs(have: NodeStateRef | None, fleet_epoch: int, row: sqlite3.Row) -> bool:
    if have is None:
        return True
    return (have.fleet_epoch, have.revision, have.sha256) != (fleet_epoch, row["revision"], row["doc_sha256"])


def _with_users(spec: NodeSpec, users: list[UserRecord]) -> list[dict[str, Any]]:
    """Insert one users resource per managed inbound right after the Xray config."""
    resources = [resource.model_dump(mode="json") for resource in spec.resources]
    configs = [index for index, resource in enumerate(resources) if resource["kind"] == "xray_config"]
    if not spec.managed_inbounds:
        return resources
    if len(configs) != 1:
        raise ServiceError("invalid_request", "Managed inbounds need exactly one xray_config resource.", 400)
    config_id = resources[configs[0]]["id"]
    active = [user for user in users if user.status == "active"]
    inserted = [_users_resource(inbound, active, config_id) for inbound in spec.managed_inbounds]
    return [*resources[: configs[0] + 1], *inserted, *resources[configs[0] + 1 :]]


def _users_resource(inbound: ManagedInbound, users: list[UserRecord], config_id: str) -> dict[str, Any]:
    if inbound.protocol == "hysteria2":
        return {
            "id": users_resource_id(inbound.tag),
            "depends_on": [config_id],
            "kind": "hysteria2_users",
            "inbound_tag": inbound.tag,
            "users": [{"user_id": user.user_id, "auth": user.uuid} for user in users],
        }
    return {
        "id": users_resource_id(inbound.tag),
        "depends_on": [config_id],
        "kind": "xray_users",
        "inbound_tag": inbound.tag,
        "users": [{"user_id": user.user_id, "uuid": user.uuid, "flow": inbound.flow} for user in users],
    }


def _user(row: Any) -> UserRecord:
    names = [column.strip() for column in _USER_COLUMNS.split(",")]
    return UserRecord.model_validate(dict(zip(names, tuple(row), strict=True)))


def _expired(user: UserRecord, now: str) -> bool:
    # Parse, never compare strings: a fractional "…:00.5Z" sorts before "…:00Z".
    return user.expire_at is not None and datetime.fromisoformat(user.expire_at) <= datetime.fromisoformat(now)
