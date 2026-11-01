"""Revision-gated receipts for per-person runtime flow blocking; never store connection data."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from meridian.control.events import EventLedger
from meridian.control.flow_block_evidence import FlowBlockEvidence
from meridian.control.models import UserRecord
from meridian.control.node_documents import RetiredUsers
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
    identities TEXT NOT NULL,
    fleet_epoch INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    PRIMARY KEY (user_id, node_id, credential_generation)
)"""


def credential_identity(credential: str) -> str:
    return hashlib.sha256(credential.encode()).hexdigest()


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
        retired: RetiredUsers = None,
    ) -> None:
        """Record every lost identity in the document mutation's transaction, once per person and node."""
        removed = [retired] if isinstance(retired, UserRecord) else retired or []
        retired_by_id = {user.user_id: user for user in removed}
        directory = {**{user.user_id: user for user in users}, **retired_by_id}
        old, new = carried(before), carried(after)
        kinds = {r["inbound_tag"]: r["kind"] for r in before if r["kind"] in {"xray_users", "hysteria2_users"}}
        gone: set[str] = set()
        lost_identities: dict[str, dict[str, str]] = {}
        credentials_changed: set[str] = set()
        for tag, entries in old.items():
            for user_id, credential in entries.items():
                replacement = new.get(tag, {}).get(user_id)
                if replacement != credential:
                    gone.add(user_id)
                    lost_identities.setdefault(user_id, {})[tag] = credential_identity(credential)
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
            if user_id in retired_by_id:
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
                "SELECT * FROM user_flow_blocks WHERE user_id = ? AND node_id = ? AND credential_generation = ?",
                (user_id, node_id, generation),
            )
            identities = json.loads(previous["identities"]) if previous is not None else {}
            identities.update(lost_identities[user_id])
            self._store.db.execute(
                "INSERT INTO user_flow_blocks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(user_id, node_id, credential_generation) DO UPDATE SET username = excluded.username,"
                " reason = excluded.reason, inbound_tags = excluded.inbound_tags, identities = excluded.identities,"
                " fleet_epoch = excluded.fleet_epoch, revision = excluded.revision,"
                " sha256 = excluded.sha256, status = excluded.status, changed_at = excluded.changed_at",
                (
                    user_id,
                    node_id,
                    user.username,
                    generation,
                    reason,
                    json.dumps(sorted(identities)),
                    json.dumps(identities, sort_keys=True),
                    required.fleet_epoch,
                    required.revision,
                    required.sha256,
                    "pending_node",
                    format_timestamp(self._clock()),
                ),
            )
            if previous is None or previous["status"] != "pending_node":
                self._event(user.username, node_id, generation, "pending_node")
        # An explicitly restored key is active even if its numeric generation
        # advanced through imports. Compare the identity, not just the generation.
        for receipt in self._rows(node_id):
            identities = json.loads(receipt["identities"])
            remaining = {
                tag: fingerprint
                for tag, fingerprint in identities.items()
                if (active_credential := new.get(tag, {}).get(receipt["user_id"])) is None
                or credential_identity(active_credential) != fingerprint
            }
            key = (receipt["user_id"], node_id, receipt["credential_generation"])
            if not remaining:
                self._store.db.execute(
                    "DELETE FROM user_flow_blocks WHERE user_id = ? AND node_id = ? AND credential_generation = ?", key
                )
            elif remaining != identities:
                self._store.db.execute(
                    "UPDATE user_flow_blocks SET inbound_tags = ?, identities = ?"
                    " WHERE user_id = ? AND node_id = ? AND credential_generation = ?",
                    (json.dumps(sorted(remaining)), json.dumps(remaining, sort_keys=True), *key),
                )
        node = self._store.row("SELECT * FROM nodes WHERE node_id = ?", (node_id,))
        assert node is not None
        evidence = FlowBlockEvidence.from_node(node, fleet_epoch=self._store.fleet_epoch, now=self._clock())
        for receipt in self._rows(node_id):
            if receipt["fleet_epoch"] != required.fleet_epoch:
                self._store.db.execute(
                    "UPDATE user_flow_blocks SET fleet_epoch = ?, revision = ?, sha256 = ?, changed_at = ?"
                    " WHERE user_id = ? AND node_id = ? AND credential_generation = ?",
                    (
                        required.fleet_epoch,
                        required.revision,
                        required.sha256,
                        format_timestamp(self._clock()),
                        receipt["user_id"],
                        node_id,
                        receipt["credential_generation"],
                    ),
                )
                self._set_status(receipt, evidence.status(required))
        self._prune()

    def hello(self, node_id: str, capabilities: list[Capability]) -> None:
        before = self._store.row("SELECT * FROM nodes WHERE node_id = ?", (node_id,))
        if before is None:
            return
        old = json.loads(before["agent_capabilities"]) if before["agent_capabilities"] is not None else []
        self._store.db.execute(
            "UPDATE nodes SET agent_capabilities = ? WHERE node_id = ?", (json.dumps(capabilities), node_id)
        )
        node = self._store.row("SELECT * FROM nodes WHERE node_id = ?", (node_id,))
        assert node is not None
        evidence = FlowBlockEvidence.from_node(node, fleet_epoch=self._store.fleet_epoch, now=self._clock())
        for row in self._rows(node_id):
            status = evidence.status(self._required(row))
            if "user_flow_blocking" in capabilities and "user_flow_blocking" not in old:
                status = "pending_node"
            self._set_status(row, status)

    def report(self, node_id: str, report: ObservedStateReport) -> None:
        node = self._store.row("SELECT * FROM nodes WHERE node_id = ?", (node_id,))
        if node is None:
            return
        evidence = FlowBlockEvidence.from_node(
            node, fleet_epoch=self._store.fleet_epoch, now=self._clock(), report=report
        )
        for row in self._rows(node_id):
            self._set_status(row, evidence.status(self._required(row)))
        self._prune()

    def read(self, user: UserRecord) -> UserFlowBlocks:
        with self._store.lock:
            nodes: list[UserFlowBlockNode] = []
            evidence_by_node: dict[str, FlowBlockEvidence] = {}
            for row in self._store.rows(
                "SELECT * FROM user_flow_blocks WHERE user_id = ? ORDER BY node_id, credential_generation",
                (user.user_id,),
            ):
                node_id = row["node_id"]
                if node_id not in evidence_by_node:
                    node = self._store.row("SELECT * FROM nodes WHERE node_id = ?", (node_id,))
                    if node is None:
                        continue
                    evidence_by_node[node_id] = FlowBlockEvidence.from_node(
                        node, fleet_epoch=self._store.fleet_epoch, now=self._clock()
                    )
                required = self._required(row)
                nodes.append(
                    UserFlowBlockNode(
                        node_id=node_id,
                        credential_generation=row["credential_generation"],
                        reason=row["reason"],
                        status=evidence_by_node[node_id].status(required),
                        required=required,
                        inbound_tags=json.loads(row["inbound_tags"]),
                    )
                )
            return UserFlowBlocks(username=user.username, credential_generation=user.credential_generation, nodes=nodes)

    @staticmethod
    def _required(row: sqlite3.Row) -> NodeStateRef:
        return NodeStateRef(fleet_epoch=row["fleet_epoch"], revision=row["revision"], sha256=row["sha256"])

    def _rows(self, node_id: str) -> list[sqlite3.Row]:
        return self._store.rows(
            "SELECT * FROM user_flow_blocks WHERE node_id = ? ORDER BY credential_generation", (node_id,)
        )

    def _set_status(self, row: sqlite3.Row, status: FlowBlockStatus) -> None:
        if row["status"] == status:
            return
        self._store.db.execute(
            "UPDATE user_flow_blocks SET status = ?, changed_at = ?"
            " WHERE user_id = ? AND node_id = ? AND credential_generation = ?",
            (status, format_timestamp(self._clock()), row["user_id"], row["node_id"], row["credential_generation"]),
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
        self._store.db.execute(
            "DELETE FROM user_flow_blocks AS old WHERE old.status = 'blocked' AND EXISTS"
            " (SELECT 1 FROM user_flow_blocks newer WHERE newer.user_id = old.user_id"
            " AND newer.node_id = old.node_id AND newer.credential_generation > old.credential_generation)"
        )
        cutoff = (datetime.fromtimestamp(self._clock(), UTC) - timedelta(days=EVENT_RETENTION_DAYS)).isoformat()
        self._store.db.execute(
            "DELETE FROM user_flow_blocks WHERE user_id NOT IN (SELECT user_id FROM users)"
            " AND (status = 'blocked' OR julianday(changed_at) < julianday(?))",
            (cutoff,),
        )
