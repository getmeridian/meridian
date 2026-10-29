"""Topology revisions and the apply fence: the service's record of every reviewed apply.

A revision is one reviewed apply: the secret-free topology intent, the plan and
review hashes, who proposed it, and its status, `proposed` -> `approved` ->
`applied` | `failed`. Proposing needs the `propose` scope and approving
`approve`; a proposer may not approve their own revision unless the token holds
`admin`, which is the deployer's one-person path (the root config token is
`admin`). Recording the result, like every change the apply itself makes, needs
`admin`.

The apply fence (`reconciler/fence.py` decides) is one row: fleet ID, committed
generation, plan hash and revision, and a lease. The service never decides who
may apply; it only replaces the row when the caller names the version it read,
so two machines can never both take the lease.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Callable, Iterator
from typing import Any

from meridian.control.errors import ServiceError
from meridian.control.models import (
    ApplyFenceRecord,
    ApplyFenceState,
    ApplyFenceSwap,
    RevisionActor,
    RevisionList,
    RevisionProposal,
    RevisionRecord,
    RevisionResult,
    RevisionSummary,
)
from meridian.control.store import Store
from meridian.control.times import format_timestamp
from meridian.control.tokens import Caller, TokenRegistry
from meridian.core.agent_types import canonical_sha256
from meridian.core.redaction import is_sensitive_key

ROOT_AUTHOR = "root"

REVISIONS_TABLE = """CREATE TABLE IF NOT EXISTS revisions (
    number INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    author_token_id TEXT,
    author_name TEXT NOT NULL,
    intent TEXT NOT NULL,
    intent_sha256 TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    review_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    approver_token_id TEXT,
    approver_name TEXT,
    approved_at TEXT,
    finished_at TEXT,
    detail TEXT NOT NULL DEFAULT ''
)"""
APPLY_FENCE_TABLE = """CREATE TABLE IF NOT EXISTS apply_fence (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL,
    record TEXT NOT NULL
)"""
_SUMMARY = (
    "number, created_at, author_token_id, author_name, intent_sha256, plan_hash, review_hash, status,"
    " approver_token_id, approver_name, approved_at, finished_at, detail"
)
# Secret forms that may hide in a value even under an innocent key.
_SECRET_VALUES = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bmrd_[0-9a-f]{12}_[0-9a-f]{64}\b"),
)


def secret_paths(value: Any, path: str = "") -> list[str]:
    """JSON paths in `value` whose key names a secret or whose text holds one; empty means secret-free."""
    found: list[str] = []
    for key, item in _children(value):
        here = f"{path}.{key}" if path else str(key)
        if isinstance(key, str) and is_sensitive_key(key):
            found.append(here)
        elif isinstance(item, str) and any(pattern.search(item) for pattern in _SECRET_VALUES):
            found.append(here)
        else:
            found.extend(secret_paths(item, here))
    return found


def _children(value: Any) -> Iterator[tuple[str | int, Any]]:
    if isinstance(value, dict):
        yield from value.items()
    elif isinstance(value, list):
        yield from enumerate(value)


class RevisionLedger:
    def __init__(
        self, store: Store, tokens: TokenRegistry, fleet_id: str, *, clock: Callable[[], float] = time.time
    ) -> None:
        self._store = store
        self._tokens = tokens
        self._fleet_id = fleet_id
        self._clock = clock
        store.create(REVISIONS_TABLE)
        store.create(APPLY_FENCE_TABLE)

    # -- revisions ------------------------------------------------------------------

    def propose(self, proposal: RevisionProposal, *, caller: Caller) -> RevisionRecord:
        leaked = secret_paths(proposal.intent)
        if leaked:
            raise ServiceError(
                "invalid_request", "The intent carries secret-shaped fields: " + ", ".join(leaked[:5]), 400
            )
        author = self._actor(caller)
        with self._store.transaction():
            cursor = self._store.db.execute(
                "INSERT INTO revisions (created_at, author_token_id, author_name, intent, intent_sha256, plan_hash,"
                " review_hash, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'proposed')",
                (
                    self._now(),
                    author.token_id,
                    author.name,
                    json.dumps(proposal.intent, sort_keys=True),
                    canonical_sha256(proposal.intent),
                    proposal.plan_hash,
                    proposal.review_hash,
                ),
            )
            number = int(cursor.lastrowid or 0)
        return self.get(number)

    def approve(self, number: int, *, caller: Caller) -> RevisionRecord:
        """Approve a proposed revision; its own proposer may do so only with an `admin` token."""
        approver = self._actor(caller)
        with self._store.transaction():
            row = self._require(number)
            if row["status"] != "proposed":
                raise ServiceError("conflict", f"Revision {number} is {row['status']}, not proposed.", 409)
            if row["author_token_id"] == caller.token_id and caller.scope != "admin":
                raise ServiceError(
                    "insufficient_scope", "Only an admin token may approve a revision it proposed itself.", 403
                )
            self._store.db.execute(
                "UPDATE revisions SET status = 'approved', approver_token_id = ?, approver_name = ?, approved_at = ?"
                " WHERE number = ?",
                (approver.token_id, approver.name, self._now(), number),
            )
        return self.get(number)

    def record_result(self, number: int, result: RevisionResult) -> RevisionRecord:
        """Mark an approved revision applied or failed; the same result again is a no-op (a lost reply's retry)."""
        with self._store.transaction():
            row = self._require(number)
            if row["status"] == result.status:
                return self.get(number)
            if row["status"] != "approved":
                raise ServiceError("conflict", f"Revision {number} is {row['status']}, not approved.", 409)
            self._store.db.execute(
                "UPDATE revisions SET status = ?, finished_at = ?, detail = ? WHERE number = ?",
                (result.status, self._now(), result.detail, number),
            )
        return self.get(number)

    def get(self, number: int) -> RevisionRecord:
        row = self._store.row(f"SELECT {_SUMMARY}, intent FROM revisions WHERE number = ?", (number,))
        if row is None:
            raise ServiceError("not_found", f"Unknown revision {number}.", 404)
        return RevisionRecord(**dict(_summary(row)), intent=json.loads(row["intent"]))

    def list_revisions(self) -> RevisionList:
        rows = self._store.rows(f"SELECT {_SUMMARY} FROM revisions ORDER BY number DESC")
        return RevisionList(revisions=[_summary(row) for row in rows])

    # -- apply fence ----------------------------------------------------------------

    def fence(self) -> ApplyFenceState:
        row = self._store.row("SELECT version, record FROM apply_fence WHERE id = 1")
        if row is None:
            return ApplyFenceState(fleet_id=self._fleet_id, version=0)
        record = ApplyFenceRecord.model_validate_json(row["record"])
        return ApplyFenceState(fleet_id=self._fleet_id, version=int(row["version"]), record=record)

    def swap_fence(self, swap: ApplyFenceSwap) -> ApplyFenceState:
        record = swap.record
        if record.fleet_id != self._fleet_id:
            raise ServiceError("invalid_request", "The fence names another fleet than this control service's.", 400)
        with self._store.transaction():
            current = self.fence()
            if current.version != swap.expected_version:
                raise ServiceError("conflict", "The apply fence changed since it was read.", 409)
            if record.revision is not None and self._status(record.revision) != "applied":
                raise ServiceError("conflict", f"Revision {record.revision} has not been applied.", 409)
            self._store.db.execute(
                "INSERT INTO apply_fence (id, version, record) VALUES (1, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET version = excluded.version, record = excluded.record",
                (current.version + 1, record.model_dump_json()),
            )
        return self.fence()

    # -- helpers --------------------------------------------------------------------

    def _actor(self, caller: Caller) -> RevisionActor:
        if caller.token_id is None:
            return RevisionActor(token_id=None, name=caller.name or ROOT_AUTHOR)
        return RevisionActor(token_id=caller.token_id, name=self._tokens.get(caller.token_id).name)

    def _require(self, number: int) -> sqlite3.Row:
        row = self._store.row("SELECT status, author_token_id FROM revisions WHERE number = ?", (number,))
        if row is None:
            raise ServiceError("not_found", f"Unknown revision {number}.", 404)
        return row

    def _status(self, number: int) -> str | None:
        row = self._store.row("SELECT status FROM revisions WHERE number = ?", (number,))
        return str(row["status"]) if row is not None else None

    def _now(self) -> str:
        return format_timestamp(self._clock())


def _summary(row: sqlite3.Row) -> RevisionSummary:
    approver = (
        RevisionActor(token_id=row["approver_token_id"], name=str(row["approver_name"]))
        if row["approver_name"] is not None
        else None
    )
    return RevisionSummary(
        number=int(row["number"]),
        created_at=str(row["created_at"]),
        author=RevisionActor(token_id=row["author_token_id"], name=str(row["author_name"])),
        intent_sha256=str(row["intent_sha256"]),
        plan_hash=str(row["plan_hash"]),
        review_hash=str(row["review_hash"]),
        status=row["status"],
        approved_by=approver,
        approved_at=row["approved_at"],
        finished_at=row["finished_at"],
        detail=str(row["detail"]),
    )
