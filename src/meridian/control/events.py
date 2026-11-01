"""The bounded local event ledger and debounced node state transitions.

append() shares the mutation's transaction; record() opens one for independent
facts. Actors are request-local, and enforcement always names system explicitly.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

from meridian.control.models import AGENT_SEEN_SECONDS, NodeStatus, UserRecord, UserStatus
from meridian.control.store import Store
from meridian.control.times import format_timestamp
from meridian.core.fleet_events import (
    EVENT_RETENTION_DAYS,
    MAX_EVENTS,
    EmptyEventDetail,
    EventDetail,
    FleetEvent,
    FleetEventKind,
    FleetEventPage,
    FleetEventQuery,
    NodeEventDetail,
    UserEventField,
    UserFieldsDetail,
    UserStateDetail,
)

EVENTS_TABLE = """CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT NOT NULL,
    kind TEXT NOT NULL,
    actor TEXT NOT NULL,
    subject TEXT NOT NULL,
    detail TEXT NOT NULL
)"""
NODE_EVENT_STATES_TABLE = """CREATE TABLE IF NOT EXISTS event_node_states (
    node_id TEXT PRIMARY KEY,
    observed_since TEXT NOT NULL,
    live INTEGER,
    apply_failed INTEGER
)"""
EVENT_TABLES = (EVENTS_TABLE, NODE_EVENT_STATES_TABLE, "CREATE INDEX IF NOT EXISTS events_time ON events(time)")
_USER_FIELDS: tuple[UserEventField, ...] = (
    "device_limit",
    "expire_at",
    "traffic_limit_bytes",
    "traffic_limit_strategy",
    "created_at",
    "traffic_reset_at",
    "lifetime_traffic_bytes",
    "language",
    "traffic_reset_anchor_day",
    "activation_duration_days",
    "renewal_interval_days",
    "renewal_interval_months",
    "renewal_limit",
    "renewals_completed",
    "renewal_anchor_at",
)


class EventLedger:
    def __init__(self, store: Store, *, clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._clock = clock
        self._local = threading.local()
        for statement in EVENT_TABLES:
            store.create(statement)

    @contextmanager
    def as_actor(self, name: str) -> Iterator[None]:
        """The caller's name for this thread's mutation hooks, restored even after refusal."""
        previous = getattr(self._local, "actor", "root")
        self._local.actor = name
        try:
            yield
        finally:
            self._local.actor = previous

    def append(
        self,
        kind: FleetEventKind,
        subject: str,
        *,
        detail: EventDetail | None = None,
        actor: str | None = None,
        now: str | None = None,
    ) -> FleetEvent:
        """Add a typed fact inside the caller's mutation transaction, then apply retention."""
        if not self._store.db.in_transaction:
            raise RuntimeError("Event append needs the mutation's transaction.")
        event = FleetEvent(
            id=1,
            time=now or format_timestamp(self._clock()),
            kind=kind,
            actor=actor if actor is not None else str(getattr(self._local, "actor", "root")),
            subject=subject,
            detail=detail if detail is not None else EmptyEventDetail(),
        )
        inserted = self._store.db.execute(
            "INSERT INTO events(time, kind, actor, subject, detail) VALUES (?, ?, ?, ?, ?)",
            (event.time, event.kind, event.actor, event.subject, event.detail.model_dump_json()),
        )
        assert inserted.lastrowid is not None
        self._prune(event.time)
        return event.model_copy(update={"id": inserted.lastrowid})

    def record(self, kind: FleetEventKind, subject: str, *, detail: EventDetail | None = None) -> FleetEvent:
        with self._store.transaction():
            return self.append(kind, subject, detail=detail)

    def list(self, *, after: int = 0, limit: int = 100) -> FleetEventPage:
        query = FleetEventQuery(after=after, limit=limit)
        cutoff = self._cutoff(format_timestamp(self._clock()))
        rows = self._store.rows(
            "SELECT * FROM events WHERE id > ? AND julianday(time) >= julianday(?) ORDER BY id LIMIT ?",
            (query.after, cutoff, query.limit + 1),
        )
        events = [
            FleetEvent.model_validate(
                {
                    "id": row["id"],
                    "time": row["time"],
                    "kind": row["kind"],
                    "actor": row["actor"],
                    "subject": row["subject"],
                    "detail": json.loads(row["detail"]),
                }
            )
            for row in rows[: query.limit]
        ]
        return FleetEventPage(
            events=events, next_cursor=events[-1].id if events else query.after, has_more=len(rows) > query.limit
        )

    def prune(self, now: str | None = None) -> None:
        with self._store.transaction():
            self._prune(now or format_timestamp(self._clock()))

    def _prune(self, now: str) -> None:
        self._store.db.execute("DELETE FROM events WHERE julianday(time) < julianday(?)", (self._cutoff(now),))
        self._store.db.execute(
            "DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT ?)", (MAX_EVENTS,)
        )

    @staticmethod
    def _cutoff(now: str) -> str:
        return (datetime.fromisoformat(now) - timedelta(days=EVENT_RETENTION_DAYS)).isoformat()

    def user_updated(
        self, before: UserRecord, after: UserRecord, *, actor: str | None = None, now: str | None = None
    ) -> None:
        """Operator changes name fields only; credential replacement is summarized without values."""
        fields = [field for field in _USER_FIELDS if getattr(before, field) != getattr(after, field)]
        if (before.uuid, before.sub_token) != (after.uuid, after.sub_token):
            fields.append("credentials")
        if before.status != after.status:
            if after.status in ("active", "disabled"):
                self.append(
                    "user.enabled" if after.status == "active" else "user.disabled",
                    after.username,
                    actor=actor,
                    now=now,
                )
            else:
                fields.append("status")
        if fields:
            self.append(
                "user.updated", after.username, detail=UserFieldsDetail(fields=sorted(fields)), actor=actor, now=now
            )

    def enforcement(self, username: str, previous: UserStatus, status: UserStatus, now: str) -> None:
        kinds: dict[UserStatus, FleetEventKind] = {
            "expired": "user.expired",
            "limited": "user.limited",
            "active": "user.restored",
        }
        self.append(kinds[status], username, actor="system", now=now, detail=UserStateDetail(previous_status=previous))

    def watch_node(self, node_id: str, now: str | None = None) -> None:
        self._store.db.execute(
            "INSERT OR IGNORE INTO event_node_states(node_id, observed_since) VALUES (?, ?)",
            (node_id, now or format_timestamp(self._clock())),
        )

    def forget_node(self, node_id: str) -> None:
        self._store.db.execute("DELETE FROM event_node_states WHERE node_id = ?", (node_id,))

    def observe_nodes(self, read_nodes: Callable[[], Iterable[NodeStatus]], now: str | None = None) -> None:
        """The 30 s timer reads liveness and report state inside the transition's transaction."""
        moment = now or format_timestamp(self._clock())
        with self._store.transaction():
            self._prune(moment)
            self._store.db.execute("DELETE FROM event_node_states WHERE node_id NOT IN (SELECT node_id FROM nodes)")
            for status in read_nodes():
                self._observe_node(status, moment)

    def _observe_node(self, status: NodeStatus, now: str) -> None:
        self.watch_node(status.node_id, now)
        row = self._store.row("SELECT * FROM event_node_states WHERE node_id = ?", (status.node_id,))
        assert row is not None
        live = status.is_live(datetime.fromisoformat(now))
        waited = (datetime.fromisoformat(now) - datetime.fromisoformat(row["observed_since"])).total_seconds()
        if live or status.last_seen_at is not None or waited >= AGENT_SEEN_SECONDS:
            if row["live"] is None or bool(row["live"]) != live:
                self.append(
                    "node.online" if live else "node.offline",
                    status.node_id,
                    actor="system",
                    now=now,
                    detail=NodeEventDetail(reason="agent_seen" if live else "live_window_elapsed"),
                )
                self._store.db.execute(
                    "UPDATE event_node_states SET live = ? WHERE node_id = ?", (live, status.node_id)
                )
        # A missing report is unavailable evidence, never an apply recovery.
        report = self._store.row("SELECT report FROM nodes WHERE node_id = ?", (status.node_id,))
        if report is not None and report["report"] is not None:
            failed = bool(status.failed)
            if (row["apply_failed"] is None and failed) or (
                row["apply_failed"] is not None and bool(row["apply_failed"]) != failed
            ):
                self.append(
                    "node.apply_failed" if failed else "node.apply_recovered",
                    status.node_id,
                    actor="system",
                    now=now,
                    detail=NodeEventDetail(
                        reason="apply_failed" if failed else "apply_recovered", failed_resources=len(status.failed)
                    ),
                )
            self._store.db.execute(
                "UPDATE event_node_states SET apply_failed = ? WHERE node_id = ?", (failed, status.node_id)
            )
