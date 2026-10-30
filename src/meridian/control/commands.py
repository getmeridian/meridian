"""Durable signed node commands, separate from desired-state composition.

The service signs the existing agent command JSON, delivers only negotiated kinds,
and remembers the first result. A revision gate is service state, never an unsigned
execution instruction on the wire. All mutations share the service's SQLite lock.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from meridian.control.command_models import (
    CommandRequest,
    CommandUnitTarget,
    NodeCommandRecord,
    NodeCommandTargets,
    ResetInboundCreate,
    RestartUnitCreate,
)
from meridian.control.errors import ServiceError, agent_error
from meridian.control.models import NodeSpec
from meridian.control.store import Store
from meridian.control.times import format_timestamp
from meridian.core.agent_desired import SystemdUnitResource
from meridian.core.agent_protocol import AgentCommandSpec, Capability, CommandResult, CommandResultAck
from meridian.core.agent_signing import COMMAND_CONTEXT
from meridian.core.agent_types import NodeStateRef
from meridian.core.fleet_events import CommandIssuedDetail, CommandResultDetail
from meridian.core.redaction import redact_diagnostic_text

if TYPE_CHECKING:
    from meridian.control.enrollment import NodeAuthority
    from meridian.control.events import EventLedger

RETENTION_SECONDS = 7 * 86400
MAX_PENDING_PER_NODE = 64
COMMAND_CAPABILITIES_TABLE = """
CREATE TABLE IF NOT EXISTS agent_command_capabilities (
    token_sha256 TEXT PRIMARY KEY,
    capabilities TEXT NOT NULL
)
"""
COMMAND_TABLES = (
    """CREATE TABLE IF NOT EXISTS commands (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        command_id TEXT NOT NULL UNIQUE,
        node_id TEXT NOT NULL,
        fleet_epoch INTEGER NOT NULL,
        key_id TEXT NOT NULL,
        body TEXT NOT NULL,
        issued_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        wait_for TEXT,
        status TEXT NOT NULL,
        result TEXT,
        finished_at INTEGER
    )""",
    "CREATE INDEX IF NOT EXISTS commands_pending ON commands (node_id, status, sequence)",
    "CREATE INDEX IF NOT EXISTS commands_finished ON commands (finished_at)",
    COMMAND_CAPABILITIES_TABLE,
)


class CommandLedger:
    def __init__(
        self,
        store: Store,
        authority: NodeAuthority,
        *,
        events: EventLedger,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._authority = authority
        self._events = events
        self._clock = clock
        self.maintain()

    def issue(self, node_id: str, request: CommandRequest) -> NodeCommandRecord:
        with self._store.transaction():
            self.prune()
            return self.enqueue(node_id, request)

    def enqueue(self, node_id: str, request: CommandRequest) -> NodeCommandRecord:
        """Insert inside the caller's transaction (also used by atomic credential cut-offs)."""
        node = self.require_node(node_id)
        targets = self.targets(node_id)
        if isinstance(request, RestartUnitCreate) and request.resource_id not in {u.resource_id for u in targets.units}:
            raise ServiceError("invalid_request", "That resource is not a running unit managed by this node.", 400)
        if isinstance(request, ResetInboundCreate) and request.inbound_tag not in targets.inbounds:
            raise ServiceError("invalid_request", "That inbound is not managed by this node.", 400)
        if request.wait_for is not None and (
            request.wait_for.fleet_epoch,
            request.wait_for.revision,
            request.wait_for.sha256,
        ) != (self._store.fleet_epoch, node["revision"], node["doc_sha256"]):
            raise ServiceError("conflict", "The revision to wait for is not this node's current revision.", 409)
        count = self._store.row(
            "SELECT count(*) AS n FROM commands WHERE node_id = ? AND status IN ('waiting', 'pending')", (node_id,)
        )
        if count is not None and count["n"] >= MAX_PENDING_PER_NODE:
            raise ServiceError(
                "command_queue_full", "This node already has 64 unanswered commands; wait for them.", 409
            )
        now = int(self._clock())
        body: dict[str, Any] = request.model_dump(mode="json", exclude={"expires_in_seconds", "wait_for"})
        body.update(
            command_id=str(uuid.uuid4()),
            fleet_epoch=self._store.fleet_epoch,
            issued_at=format_timestamp(now),
            expires_at=format_timestamp(now + request.expires_in_seconds),
        )
        body["signature"] = self._authority.sign(body, COMMAND_CONTEXT)
        command = AgentCommandSpec.model_validate(body).root
        command_id = command.command_id
        self._store.db.execute(
            "INSERT INTO commands (command_id, node_id, fleet_epoch, key_id, body, issued_at, expires_at,"
            " wait_for, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                command_id,
                node_id,
                self._store.fleet_epoch,
                self._authority.service_key_id,
                json.dumps(body),
                now,
                now + request.expires_in_seconds,
                request.wait_for.model_dump_json() if request.wait_for is not None else None,
                "waiting" if request.wait_for is not None else "pending",
            ),
        )
        self._events.append(
            "command.issued", node_id, detail=CommandIssuedDetail(command_id=command_id, kind=command.kind)
        )
        applied = json.loads(node["report"]).get("applied") if node["report"] else None
        if applied is not None:
            self.release_ready(node_id, NodeStateRef.model_validate(applied))
        self._store.changed.notify_all()
        return self.read(node_id, command_id)

    def get(self, node_id: str, command_id: str) -> NodeCommandRecord:
        with self._store.transaction():
            self.prune()
            return self.read(node_id, command_id)

    def read(self, node_id: str, command_id: str) -> NodeCommandRecord:
        row = self._store.row("SELECT * FROM commands WHERE node_id = ? AND command_id = ?", (node_id, command_id))
        if row is None:
            raise ServiceError("not_found", "Unknown node command.", 404)
        return self.record(row)

    def pending(self, node_id: str, capabilities: tuple[Capability, ...]) -> list[dict[str, Any]]:
        with self._store.transaction():
            self.prune()
            rows = self._store.rows(
                "SELECT body FROM commands WHERE node_id = ? AND status = 'pending' ORDER BY sequence", (node_id,)
            )
            bodies = [json.loads(row["body"]) for row in rows]
            return [body for body in bodies if body["kind"] in capabilities][:MAX_PENDING_PER_NODE]

    def release_ready(self, node_id: str, applied: NodeStateRef | None) -> None:
        """Release gates inside the report's transaction, so a waiting poll wakes after that report commits."""
        if applied is None:
            return
        for row in self._store.rows(
            "SELECT command_id, wait_for FROM commands WHERE node_id = ? AND status = 'waiting'", (node_id,)
        ):
            wanted = NodeStateRef.model_validate_json(row["wait_for"])
            if applied.fleet_epoch == wanted.fleet_epoch and (
                applied.revision > wanted.revision
                or (applied.revision == wanted.revision and applied.sha256 == wanted.sha256)
            ):
                self._store.db.execute(
                    "UPDATE commands SET status = 'pending' WHERE command_id = ?", (row["command_id"],)
                )
                self._store.changed.notify_all()

    def accept_result(self, node_id: str, result: CommandResult) -> CommandResultAck:
        with self._store.transaction():
            self.require_node(node_id)
            row = self._store.row("SELECT * FROM commands WHERE command_id = ?", (result.command_id,))
            if row is None or row["node_id"] != node_id:
                raise agent_error("invalid_request", "The command does not belong to this node.")
            self.prune()
            current = self.read(node_id, result.command_id)
            if current.status in {"pending", "waiting"}:
                if current.status == "waiting":
                    raise agent_error("invalid_request", "The command is still waiting for its revision.")
                if result.effect is not None and current.command.kind != "reset_inbound":
                    raise agent_error("invalid_request", "Only an inbound reset reports a reset effect.")
                if current.command.kind == "reset_inbound" and result.status == "succeeded" and result.effect is None:
                    raise agent_error(
                        "invalid_request", "A successful inbound reset must disclose whether the core restarted."
                    )
                if result.error is not None:
                    known = [
                        str(value)
                        for user in self._store.rows("SELECT username, user_id, uuid, sub_token FROM users")
                        for value in user
                    ]
                    message = redact_diagnostic_text(" ".join(result.error.message.split()), known)[:300]
                    error = result.error.model_copy(update={"message": message or "The command failed."})
                    result = result.model_copy(update={"error": error})
                self._store.db.execute(
                    "UPDATE commands SET status = ?, result = ?, finished_at = ? WHERE command_id = ?",
                    (result.status, result.model_dump_json(), int(self._clock()), result.command_id),
                )
                self._events.append(
                    "command.result",
                    node_id,
                    actor="system",
                    detail=CommandResultDetail(
                        command_id=result.command_id,
                        kind=current.command.kind,
                        status=result.status,
                        effect=result.effect,
                    ),
                )
                self._store.changed.notify_all()
        return CommandResultAck(command_id=result.command_id)

    def maintain(self) -> None:
        with self._store.transaction():
            self.prune()

    def expire_node(self, node_id: str) -> None:
        """Called in node deletion's transaction; a replacement never inherits pending operations."""
        expired = self._store.rows(
            "SELECT command_id, node_id, body FROM commands WHERE node_id = ? AND status IN ('waiting', 'pending')",
            (node_id,),
        )
        self._store.db.execute(
            "UPDATE commands SET status = 'expired', finished_at = ? WHERE node_id = ?"
            " AND status IN ('waiting', 'pending')",
            (int(self._clock()), node_id),
        )
        self._record_expired(expired)

    def prune(self) -> None:
        """Expire unanswered commands and discard finished rows older than seven days, inside a transaction."""
        now = self._clock()
        expired = self._store.rows(
            "SELECT command_id, node_id, body FROM commands WHERE status IN ('pending', 'waiting')"
            " AND (expires_at <= ? OR fleet_epoch != ? OR key_id != ?)",
            (now, self._store.fleet_epoch, self._authority.service_key_id),
        )
        self._store.db.execute(
            "UPDATE commands SET status = 'expired', finished_at = expires_at WHERE status IN ('pending', 'waiting')"
            " AND (expires_at <= ? OR fleet_epoch != ? OR key_id != ?)",
            (now, self._store.fleet_epoch, self._authority.service_key_id),
        )
        self._record_expired(expired)
        self._store.db.execute("DELETE FROM commands WHERE finished_at < ?", (now - RETENTION_SECONDS,))

    def _record_expired(self, rows: list[sqlite3.Row]) -> None:
        for row in rows:
            command = AgentCommandSpec.model_validate_json(row["body"]).root
            self._events.append(
                "command.result",
                row["node_id"],
                actor="system",
                detail=CommandResultDetail(command_id=command.command_id, kind=command.kind, status="expired"),
            )

    def require_node(self, node_id: str) -> sqlite3.Row:
        row = self._store.row("SELECT * FROM nodes WHERE node_id = ?", (node_id,))
        if row is None:
            raise ServiceError("not_found", "Unknown node.", 404)
        return row

    def targets(self, node_id: str) -> NodeCommandTargets:
        node = self.require_node(node_id)
        spec = NodeSpec.model_validate_json(node["spec"]) if node["spec"] else NodeSpec(resources=[])
        session = self._store.row(
            "SELECT c.capabilities FROM agent_command_capabilities c JOIN agent_sessions s"
            " ON s.token_sha256 = c.token_sha256 WHERE s.node_id = ? AND s.expires_at > ?"
            " ORDER BY c.rowid DESC LIMIT 1",
            (node_id, self._clock()),
        )
        return NodeCommandTargets(
            node_id=node_id,
            units=[
                CommandUnitTarget(resource_id=resource.id, name=resource.name, core=resource.name == "netcore.service")
                for resource in spec.resources
                if isinstance(resource, SystemdUnitResource)
                and resource.name != "netagent.service"
                and resource.state == "started"
            ],
            inbounds=[inbound.tag for inbound in spec.managed_inbounds],
            capabilities=json.loads(session["capabilities"]) if session is not None else [],
        )

    @staticmethod
    def record(row: sqlite3.Row) -> NodeCommandRecord:
        return NodeCommandRecord(
            node_id=row["node_id"],
            command=AgentCommandSpec.model_validate_json(row["body"]).root,
            status=row["status"],
            wait_for=NodeStateRef.model_validate_json(row["wait_for"]) if row["wait_for"] else None,
            result=CommandResult.model_validate_json(row["result"]) if row["result"] else None,
        )
