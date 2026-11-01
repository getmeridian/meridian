"""Revision-gated receipts for per-person runtime flow blocking; never store connection data."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from meridian.control.events import EventLedger
from meridian.control.models import AGENT_SEEN_SECONDS, UserRecord
from meridian.control.store import Store
from meridian.control.times import format_timestamp
from meridian.core.agent_protocol import Capability, ObservedStateReport
from meridian.core.agent_types import NodeStateRef
from meridian.core.client_sessions import FlowBlockReason, FlowBlockStatus, UserFlowBlockNode, UserFlowBlocks
from meridian.core.fleet_events import EVENT_RETENTION_DAYS, UserFlowBlockDetail

FLOW_BLOCKS_TABLE = """CREATE TABLE IF NOT EXISTS user_flow_blocks (
    user_id TEXT NOT NULL,
    node_id TEXT NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
    username TEXT NOT NULL,
    credential_generation INTEGER NOT NULL,
    reason TEXT NOT NULL,
    inbound_tags TEXT NOT NULL,
    fleet_epoch INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    PRIMARY KEY (user_id, node_id)
)"""


def carried(resources: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    return {
        resource["inbound_tag"]: {
            user["user_id"]: user["auth" if resource["kind"] == "hysteria2_users" else "uuid"]
            for user in resource["users"]
        }
        for resource in resources
        if resource["kind"] in {"xray_users", "hysteria2_users"}
    }


class FlowBlockLedger:
    def __init__(self, store: Store, events: EventLedger, *, clock: Callable[[], float]) -> None:
        self._store = store
        self._events = events
        self._clock = clock

    def changed_document(
        self,
        node_id: str,
        before: list[dict[str, Any]],
        after: list[dict[str, Any]],
        required: NodeStateRef,
        users: list[UserRecord],
        retired: UserRecord | None = None,
    ) -> None:
        """Record every lost identity in the document mutation's transaction, once per person and node."""
        directory = {user.user_id: user for user in users}
        if retired is not None:
            directory[retired.user_id] = retired
        old, new = carried(before), carried(after)
        kinds = {r["inbound_tag"]: r["kind"] for r in before if r["kind"] in {"xray_users", "hysteria2_users"}}
        gone: set[str] = set()
        lost_tags: dict[str, list[str]] = {}
        credentials_changed: set[str] = set()
        for tag, entries in old.items():
            for user_id, credential in entries.items():
                replacement = new.get(tag, {}).get(user_id)
                if replacement != credential:
                    gone.add(user_id)
                    lost_tags.setdefault(user_id, []).append(tag)
                    if replacement is not None:
                        credentials_changed.add(user_id)
                    current = directory.get(user_id)
                    if current is not None and credential != (
                        current.uuid.lower() if kinds[tag] == "xray_users" else current.uuid
                    ):
                        credentials_changed.add(user_id)
        for user_id in sorted(gone):
            user = directory.get(user_id)
            if user is None:
                continue
            reason: FlowBlockReason = "access_changed"
            generation = max(1, user.credential_generation - int(user_id in credentials_changed))
            if retired is not None and retired.user_id == user_id:
                reason = "removed"
            elif user.status == "disabled":
                reason = "disabled"
            elif user.status == "expired":
                reason = "expired"
            elif user.status == "limited":
                reason = "limited"
            elif user_id in credentials_changed:
                reason = "credentials"
            previous = self._store.row(
                "SELECT * FROM user_flow_blocks WHERE user_id = ? AND node_id = ?", (user_id, node_id)
            )
            scope = set(lost_tags[user_id])
            if previous is not None:
                scope.update(json.loads(previous["inbound_tags"]))
                if previous["credential_generation"] < generation:
                    generation, reason = previous["credential_generation"], previous["reason"]
            self._store.db.execute(
                "INSERT INTO user_flow_blocks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(user_id, node_id) DO UPDATE SET credential_generation = excluded.credential_generation,"
                " reason = excluded.reason, inbound_tags = excluded.inbound_tags,"
                " fleet_epoch = excluded.fleet_epoch, revision = excluded.revision,"
                " sha256 = excluded.sha256, status = excluded.status, changed_at = excluded.changed_at",
                (
                    user_id,
                    node_id,
                    user.username,
                    generation,
                    reason,
                    json.dumps(sorted(scope)),
                    required.fleet_epoch,
                    required.revision,
                    required.sha256,
                    "pending_node",
                    format_timestamp(self._clock()),
                ),
            )
            self._event(user.username, node_id, generation, "pending_node")
        # Restoring the same generation deliberately permits it again. Rotated generations remain revoked.
        for user in users:
            if user.status == "active" and user.user_id not in gone:
                receipt = self._store.row(
                    "SELECT * FROM user_flow_blocks WHERE user_id = ? AND node_id = ?", (user.user_id, node_id)
                )
                if receipt is not None and receipt["credential_generation"] == user.credential_generation:
                    remaining = [
                        tag for tag in json.loads(receipt["inbound_tags"]) if user.user_id not in new.get(tag, {})
                    ]
                    if not remaining:
                        self._store.db.execute(
                            "DELETE FROM user_flow_blocks WHERE user_id = ? AND node_id = ?", (user.user_id, node_id)
                        )
                    else:
                        self._store.db.execute(
                            "UPDATE user_flow_blocks SET inbound_tags = ? WHERE user_id = ? AND node_id = ?",
                            (json.dumps(remaining), user.user_id, node_id),
                        )
        self._prune()

    def hello(self, node_id: str, capabilities: list[Capability]) -> None:
        self._store.db.execute(
            "UPDATE nodes SET agent_capabilities = ? WHERE node_id = ?", (json.dumps(capabilities), node_id)
        )
        status: FlowBlockStatus = "pending_node" if "user_flow_blocking" in capabilities else "agent_too_old"
        for row in self._rows(node_id):
            self._set_status(row, status)

    def report(self, node_id: str, report: ObservedStateReport) -> None:
        node = self._store.row("SELECT * FROM nodes WHERE node_id = ?", (node_id,))
        if node is None:
            return
        capabilities = json.loads(node["agent_capabilities"]) if node["agent_capabilities"] is not None else None
        guard = report.user_flow_blocking
        current = (
            NodeStateRef(fleet_epoch=self._store.fleet_epoch, revision=node["revision"], sha256=node["doc_sha256"])
            if node["revision"]
            else None
        )
        ready = (
            capabilities is not None
            and "user_flow_blocking" in capabilities
            and guard is not None
            and guard.status == "ready"
            and guard.document == current
            and report.applied == current
            and all(resource.status == "applied" for resource in report.resources)
        )
        for row in self._rows(node_id):
            required = NodeStateRef(fleet_epoch=row["fleet_epoch"], revision=row["revision"], sha256=row["sha256"])
            status: FlowBlockStatus = "pending_node"
            if capabilities is not None and "user_flow_blocking" not in capabilities:
                status = "agent_too_old"
            elif (
                ready
                and current is not None
                and (
                    current.fleet_epoch > required.fleet_epoch
                    or current.fleet_epoch == required.fleet_epoch
                    and current.revision >= required.revision
                )
            ):
                status = "blocked"
            self._set_status(row, status)
        self._prune()

    def read(self, user: UserRecord) -> UserFlowBlocks:
        with self._store.lock:
            nodes: list[UserFlowBlockNode] = []
            for row in self._store.rows(
                "SELECT b.*, n.last_seen_at, n.agent_capabilities, n.report,"
                " n.revision AS node_revision, n.doc_sha256 AS node_sha256 FROM user_flow_blocks b"
                " JOIN nodes n ON n.node_id = b.node_id WHERE b.user_id = ? ORDER BY b.node_id",
                (user.user_id,),
            ):
                status: FlowBlockStatus = row["status"]
                if row["agent_capabilities"] is not None and "user_flow_blocking" not in json.loads(
                    row["agent_capabilities"]
                ):
                    status = "agent_too_old"
                elif status == "blocked":
                    seen = datetime.fromisoformat(row["last_seen_at"]).timestamp() if row["last_seen_at"] else 0
                    report = json.loads(row["report"]) if row["report"] else {}
                    guard = report.get("user_flow_blocking") or {}
                    current = {
                        "fleet_epoch": self._store.fleet_epoch,
                        "revision": row["node_revision"],
                        "sha256": row["node_sha256"],
                    }
                    if (
                        not 0 <= self._clock() - seen < AGENT_SEEN_SECONDS
                        or report.get("applied") != current
                        or guard.get("document") != current
                        or guard.get("status") != "ready"
                    ):
                        status = "pending_node"
                nodes.append(
                    UserFlowBlockNode(
                        node_id=row["node_id"],
                        credential_generation=row["credential_generation"],
                        reason=row["reason"],
                        status=status,
                        required=NodeStateRef(
                            fleet_epoch=row["fleet_epoch"], revision=row["revision"], sha256=row["sha256"]
                        ),
                        inbound_tags=json.loads(row["inbound_tags"]),
                    )
                )
            return UserFlowBlocks(username=user.username, credential_generation=user.credential_generation, nodes=nodes)

    def _rows(self, node_id: str) -> list[sqlite3.Row]:
        return self._store.rows("SELECT * FROM user_flow_blocks WHERE node_id = ?", (node_id,))

    def _set_status(self, row: sqlite3.Row, status: FlowBlockStatus) -> None:
        if row["status"] == status:
            return
        self._store.db.execute(
            "UPDATE user_flow_blocks SET status = ?, changed_at = ? WHERE user_id = ? AND node_id = ?",
            (status, format_timestamp(self._clock()), row["user_id"], row["node_id"]),
        )
        self._event(row["username"], row["node_id"], row["credential_generation"], status)

    def _event(self, username: str, node_id: str, generation: int, status: FlowBlockStatus) -> None:
        self._events.append(
            "user.flow_blocking",
            username,
            actor="system",
            detail=UserFlowBlockDetail(node_id=node_id, credential_generation=generation, status=status),
        )

    def _prune(self) -> None:
        cutoff = (datetime.fromtimestamp(self._clock(), UTC) - timedelta(days=EVENT_RETENTION_DAYS)).isoformat()
        self._store.db.execute(
            "DELETE FROM user_flow_blocks WHERE user_id NOT IN (SELECT user_id FROM users)"
            " AND (status = 'blocked' OR julianday(changed_at) < julianday(?))",
            (cutoff,),
        )
