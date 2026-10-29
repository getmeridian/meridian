"""The control service's state and logic, independent of HTTP.

One SQLite file holds the fleet identity, the CA and signing key
(`enrollment.NodeAuthority`), node specs and the signed documents composed from
them, user records, client endpoints, traffic totals (`traffic.TrafficLedger`),
and scoped API tokens (`tokens.TokenRegistry`).
Every change that alters a node's resources composes a new revision, signs it,
and wakes that node's waiting poll.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
import uuid as uuid_module
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from meridian.control.enrollment import NodeAuthority
from meridian.control.errors import ServiceError
from meridian.control.formats.entries import SubscriptionUser
from meridian.control.formats.selector import SUFFIX_FORMATS, select_format
from meridian.control.models import (
    ClientEndpoint,
    EnrollmentGrant,
    LimitStrategy,
    ManagedInbound,
    NodeSpec,
    NodeStatus,
    ServiceHealth,
    ServiceSettings,
    UserImport,
    UserImportBatch,
    UserImportResult,
    UserRecord,
    UserStatus,
    UserUpdate,
    users_resource_id,
)
from meridian.control.owner import UI_TABLES, OwnerAuth
from meridian.control.revisions import APPLY_FENCE_TABLE, REVISIONS_TABLE, RevisionLedger
from meridian.control.store import Store
from meridian.control.subscriptions import SubscriptionResponse, render_subscription, unknown_subscription
from meridian.control.times import utc_now
from meridian.control.tokens import API_TOKENS_TABLE, TokenRegistry
from meridian.control.traffic import TrafficLedger
from meridian.core.agent_desired import DesiredNodeState
from meridian.core.agent_protocol import ObservedStateReport, PollRequest
from meridian.core.agent_signing import DESIRED_STATE_CONTEXT
from meridian.core.agent_types import AGENT_PROTOCOL, NodeStateRef, canonical_sha256

SCHEMA_VERSION = 8
MAX_WAIT_SECONDS = 55
_SCHEMA = """
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
    traffic_reset_at TEXT,
    traffic_carried_bytes INTEGER NOT NULL DEFAULT 0
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
    (
        "ALTER TABLE users ADD COLUMN traffic_offset_bytes INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN traffic_reset_at TEXT",
    ),
    (API_TOKENS_TABLE,),
    (REVISIONS_TABLE, APPLY_FENCE_TABLE),
    ("ALTER TABLE users ADD COLUMN traffic_carried_bytes INTEGER NOT NULL DEFAULT 0",),
    # 6 -> 7 belongs to encrypted snapshots (the backup tables), merged separately; their
    # ledger creates its tables at start either way, so a database that skipped it is whole.
    (),
    # 7 -> 8: the web UI's owner, sessions, and setup links (`owner.py`).
    UI_TABLES,
)
_USER_FIELDS = (
    "username",
    "user_id",
    "uuid",
    "sub_token",
    "status",
    "created_at",
    "expire_at",
    "traffic_limit_bytes",
    "traffic_limit_strategy",
    "traffic_reset_at",
)
# A user's stored fields plus their lifetime traffic: what the ledger counted and what an
# import carried in from before (`traffic_carried_bytes`). `used` is the growth since the
# last reset (lifetime minus the offset the reset, or the import, recorded).
_USER_SELECT = (
    "SELECT "
    + ", ".join(f"u.{name}" for name in _USER_FIELDS)
    + ", COALESCE(t.uplink_bytes + t.downlink_bytes, 0) + u.traffic_carried_bytes AS lifetime,"
    " u.traffic_offset_bytes, t.last_seen_at"
    " FROM users u LEFT JOIN traffic_totals t ON t.user_id = u.user_id"
)


class ControlService:
    """Thread-safe service state over one SQLite database."""

    def __init__(self, database: Path, *, fleet_id: str, version: str, clock: Callable[[], float] = time.time) -> None:
        self._version = version
        self._store = Store(database)
        self._lock = self._store.lock
        self._changed = self._store.changed
        self._db = self._store.db
        self._store.create(_SCHEMA)
        # Users read their traffic from the ledger's table, so it exists before any user query.
        self.traffic = TrafficLedger(self._store)
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
        self.fleet_id = fleet_id
        self.authority = NodeAuthority(self._store, fleet_id, clock=clock)
        self.tokens = TokenRegistry(self._store, clock=clock)
        self.revisions = RevisionLedger(self._store, self.tokens, fleet_id, clock=clock)
        self.owner = OwnerAuth(self._store, clock=clock)
        with self._transaction():
            # Documents signed by an earlier signing certificate are signed again.
            self._compose_all()

    # -- identity -----------------------------------------------------------------

    @property
    def fleet_epoch(self) -> int:
        return self._store.fleet_epoch

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
            self.authority.forget_node(node_id)

    def grant_enrollment(self, node_id: str) -> EnrollmentGrant:
        with self._transaction():
            self._ensure_node(node_id)
        return self.authority.grant_enrollment(node_id)

    def node_seen(self, node_id: str, agent_version: str) -> None:
        """Record a hello: the agent is alive and runs `agent_version`."""
        with self._transaction():
            self._ensure_node(node_id)
            self._db.execute(
                "UPDATE nodes SET last_seen_at = ?, agent_version = ? WHERE node_id = ?",
                (utc_now(), agent_version, node_id),
            )

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

    def list_nodes(self) -> list[NodeStatus]:
        rows = self._store.rows("SELECT node_id FROM nodes ORDER BY node_id")
        return [self.node_status(str(row["node_id"])) for row in rows]

    def poll(self, node_id: str, request: PollRequest, *, max_wait: float = MAX_WAIT_SECONDS) -> dict[str, Any]:
        """Wait up to `wait_seconds` (at most `max_wait`) for a document newer than `have`; return the response."""
        deadline = time.monotonic() + max(0.0, min(request.wait_seconds, MAX_WAIT_SECONDS, max_wait))
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

    def import_users(self, batch: UserImportBatch) -> UserImportResult:
        """Create users with the credentials, status, limits, and usage another control plane gave them.

        All or nothing, and idempotent: a user that already exists with the same UUID and
        token is left as it is (the first import wins); one with other credentials, or
        credentials another user holds, refuses the whole batch.
        """
        created: list[str] = []
        unchanged: list[str] = []
        with self._transaction():
            now = utc_now()
            for item in batch.users:
                uuid, sub_token = item.uuid.lower(), item.sub_token
                existing = self.get_user(item.username)
                if existing is not None:
                    if (existing.uuid, existing.sub_token) != (uuid, sub_token):
                        raise ServiceError(
                            "conflict", f"User {item.username!r} already exists with other credentials.", 409
                        )
                    unchanged.append(item.username)
                    continue
                holder = self._db.execute(
                    "SELECT username FROM users WHERE uuid = ? OR sub_token = ?", (uuid, sub_token)
                ).fetchone()
                if holder is not None:
                    raise ServiceError(
                        "conflict", f"User {item.username!r} has credentials that {holder[0]!r} already holds.", 409
                    )
                # Carried use counts as lifetime traffic; the offset leaves exactly the period's use toward the limit.
                carried = max(item.lifetime_traffic_bytes, item.used_traffic_bytes)
                self._db.execute(
                    "INSERT INTO users (username, user_id, uuid, sub_token, status, created_at, expire_at,"
                    " traffic_limit_bytes, traffic_limit_strategy, traffic_offset_bytes, traffic_reset_at,"
                    " traffic_carried_bytes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        item.username,
                        f"u{secrets.token_hex(8)}",
                        uuid,
                        sub_token,
                        item.status,
                        _whole_seconds(item.created_at) or now,
                        _whole_seconds(item.expire_at),
                        item.traffic_limit_bytes,
                        item.traffic_limit_strategy,
                        carried - item.used_traffic_bytes,
                        _period_began(item, now),
                        carried,
                    ),
                )
                created.append(item.username)
            self._enforce(now)
            self._compose_all()
        return UserImportResult(created=created, unchanged=unchanged)

    def update_user(self, username: str, update: UserUpdate) -> UserRecord:
        """Apply the operator's change; extending an expiry or raising a limit makes the user active again."""
        with self._transaction():
            user = self._require_user(username)
            changes = update.model_dump(include=update.model_fields_set)
            if "status" in changes and changes["status"] is None:
                del changes["status"]
            for field in ("traffic_limit_bytes", "traffic_limit_strategy"):
                if field in changes and changes[field] is None:
                    del changes[field]
            # Enforcement below turns an expired or limited user active again once the change lifts it.
            merged = user.model_copy(update=changes)
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

    def reset_user_traffic(self, username: str) -> UserRecord:
        """Start the user's traffic period now; a user limited by it becomes active again."""
        with self._transaction():
            self._reset_traffic(self._require_user(username), utc_now())
            self._enforce(utc_now())
            self._compose_all()
            return self._require_user(username)

    def enforce(self, now: str | None = None) -> list[str]:
        """Apply expiry, traffic limits, and periodic resets; return who changed status."""
        with self._transaction():
            changed = self._enforce(now or utc_now())
            if changed:
                self._compose_all()
            return changed

    def get_user(self, username: str) -> UserRecord | None:
        with self._lock:
            row = self._db.execute(f"{_USER_SELECT} WHERE u.username = ?", (username,)).fetchone()
        return _user(row) if row else None

    def list_users(self) -> list[UserRecord]:
        with self._lock:
            rows = self._db.execute(f"{_USER_SELECT} ORDER BY u.username").fetchall()
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
        changed: list[str] = []
        for user in self.list_users():
            start = period_start(user.traffic_limit_strategy, datetime.fromisoformat(now))
            if start is not None and (
                user.traffic_reset_at is None or datetime.fromisoformat(user.traffic_reset_at) < start
            ):
                self._reset_traffic(user, now)
                user = self._require_user(user.username)
            status = _enforced_status(user, now)
            if status != user.status:
                self._db.execute("UPDATE users SET status = ? WHERE username = ?", (status, user.username))
                changed.append(user.username)
        return changed

    def _reset_traffic(self, user: UserRecord, now: str) -> None:
        self._db.execute(
            "UPDATE users SET traffic_offset_bytes = ?, traffic_reset_at = ? WHERE username = ?",
            (user.lifetime_traffic_bytes, now, user.username),
        )

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
            row = self._db.execute(f"{_USER_SELECT} WHERE u.sub_token = ?", (sub_token,)).fetchone()
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
            used_bytes=record.used_traffic_bytes,
            limit_bytes=record.traffic_limit_bytes,
            lifetime_used_bytes=record.lifetime_traffic_bytes,
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
        signed_by = json.loads(row["document"])["signature"]["key_id"] if row["document"] else None
        if resources_sha == row["doc_sha256"] and signed_by == self.authority.service_key_id:
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
        document["signature"] = self.authority.sign(document, DESIRED_STATE_CONTEXT)
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
        with self._store.transaction():
            yield

    def _meta(self, key: str) -> str | None:
        return self._store.meta(key)

    def _set_meta(self, key: str, value: str) -> None:
        self._store.set_meta(key, value)

    def _ensure_node(self, node_id: str) -> None:
        self._db.execute("INSERT OR IGNORE INTO nodes (node_id) VALUES (?)", (node_id,))

    def _touch(self, node_id: str) -> None:
        self._db.execute("UPDATE nodes SET last_seen_at = ? WHERE node_id = ?", (utc_now(), node_id))

    def _node(self, node_id: str) -> sqlite3.Row | None:
        return self._store.row("SELECT * FROM nodes WHERE node_id = ?", (node_id,))


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
    *stored, lifetime, offset, online_at = tuple(row)
    return UserRecord.model_validate(
        {
            **dict(zip(_USER_FIELDS, stored, strict=True)),
            "lifetime_traffic_bytes": lifetime,
            # A ledger that lost totals (a rebuilt database) never shows negative use.
            "used_traffic_bytes": max(0, lifetime - offset),
            "online_at": online_at,
        }
    )


def _enforced_status(user: UserRecord, now: str) -> UserStatus:
    """The status enforcement gives a user; `disabled` is the operator's and never changes."""
    if user.status == "disabled":
        return "disabled"
    if _expired(user, now):
        return "expired"
    over = user.traffic_limit_bytes > 0 and user.used_traffic_bytes >= user.traffic_limit_bytes
    return "limited" if over else "active"


def period_start(strategy: LimitStrategy, now: datetime) -> datetime | None:
    """The start of the traffic period *now* falls in (UTC), or None when traffic never resets."""
    midnight = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    if strategy == "day":
        return midnight
    if strategy == "week":
        return midnight - timedelta(days=midnight.weekday())
    if strategy == "month":
        return midnight.replace(day=1)
    return None


def _whole_seconds(timestamp: str | None) -> str | None:
    """A protocol timestamp without its fraction, as the service writes every time."""
    return f"{timestamp[:19]}Z" if timestamp else None


def _period_began(item: UserImport, now: str) -> str | None:
    """When an imported user's current traffic period began.

    The source's last reset is kept only inside the current period: an older one would
    make the next enforcement reset the imported use at once. Otherwise the period runs
    on from the import with the use the source counted, until its next boundary.
    """
    reset_at = _whole_seconds(item.traffic_reset_at)
    start = period_start(item.traffic_limit_strategy, datetime.fromisoformat(now))
    if start is None:
        return reset_at
    if reset_at is not None and datetime.fromisoformat(reset_at) >= start:
        return reset_at
    return now


def _expired(user: UserRecord, now: str) -> bool:
    # Parse, never compare strings: a fractional "…:00.5Z" sorts before "…:00Z".
    return user.expire_at is not None and datetime.fromisoformat(user.expire_at) <= datetime.fromisoformat(now)
