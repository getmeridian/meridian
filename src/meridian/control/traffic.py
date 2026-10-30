"""Per-user traffic totals from agents' cumulative `stats` reports.

Each node keeps, per counter epoch, the last seq and per-user totals it
reported; only growth past those totals is counted, so a resent or older
report is acknowledged without counting and a flaky link never double-counts.
A new counter epoch starts from zero beside the others: epoch IDs are random,
so a late report from an epoch the agent left (or one never seen) is counted
against its own baselines and never retires the live one. Only the
`KEPT_EPOCHS` most recently reported epochs keep baselines; older ones are
retired and never count again (the newest `REMEMBERED_RETIRED_EPOCHS` of
them per node are remembered). Baselines outlive node deletion: a re-added
node whose agent kept its counter store must not count its totals twice.
Nothing here records where a client connected from.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from meridian.control.errors import ServiceError, agent_error
from meridian.control.models import (
    NodeDailyUsage,
    NodeTrafficDay,
    PrivacySettings,
    TrafficDailySummary,
    TrafficSummary,
    UserTrafficDay,
    UserTrafficTotal,
    UserUsage,
)
from meridian.control.privacy import history_cutoff, prune_history_rows, read_privacy
from meridian.control.store import Store
from meridian.control.times import utc_now
from meridian.core.agent_protocol import StatsAck, StatsReport
from meridian.core.traffic import NodeTrafficAccounting, NodeTrafficEvidence

_SCHEMA = """
CREATE TABLE IF NOT EXISTS traffic_totals (
    user_id TEXT PRIMARY KEY,
    uplink_bytes INTEGER NOT NULL,
    downlink_bytes INTEGER NOT NULL,
    last_seen_at TEXT
);
CREATE TABLE IF NOT EXISTS traffic_reports (
    node_id TEXT NOT NULL,
    counter_epoch TEXT NOT NULL,
    seq INTEGER NOT NULL,
    arrival INTEGER NOT NULL,
    PRIMARY KEY (node_id, counter_epoch)
);
CREATE TABLE IF NOT EXISTS traffic_counters (
    node_id TEXT NOT NULL,
    counter_epoch TEXT NOT NULL,
    user_id TEXT NOT NULL,
    uplink_bytes INTEGER NOT NULL,
    downlink_bytes INTEGER NOT NULL,
    PRIMARY KEY (node_id, counter_epoch, user_id)
);
CREATE TABLE IF NOT EXISTS traffic_retired_epochs (
    node_id TEXT NOT NULL,
    counter_epoch TEXT NOT NULL,
    PRIMARY KEY (node_id, counter_epoch)
);
"""
# Schema 8 -> 9: baselines per (node, counter epoch) instead of one live epoch per node.
PER_EPOCH_BASELINES: tuple[str, ...] = (
    "CREATE TABLE traffic_reports_v9 (node_id TEXT NOT NULL, counter_epoch TEXT NOT NULL, seq INTEGER NOT NULL,"
    " arrival INTEGER NOT NULL, PRIMARY KEY (node_id, counter_epoch))",
    "INSERT INTO traffic_reports_v9 SELECT node_id, counter_epoch, seq, 1 FROM traffic_reports",
    "CREATE TABLE traffic_counters_v9 (node_id TEXT NOT NULL, counter_epoch TEXT NOT NULL, user_id TEXT NOT NULL,"
    " uplink_bytes INTEGER NOT NULL, downlink_bytes INTEGER NOT NULL, PRIMARY KEY (node_id, counter_epoch, user_id))",
    "INSERT INTO traffic_counters_v9 SELECT c.node_id, r.counter_epoch, c.user_id, c.uplink_bytes, c.downlink_bytes"
    " FROM traffic_counters c JOIN traffic_reports r ON r.node_id = c.node_id",
    "DROP TABLE traffic_counters",
    "DROP TABLE traffic_reports",
    "ALTER TABLE traffic_counters_v9 RENAME TO traffic_counters",
    "ALTER TABLE traffic_reports_v9 RENAME TO traffic_reports",
)
# Epochs per node that keep baselines: the live one plus a few a late report may still name.
KEPT_EPOCHS = 3
# Retired epochs remembered per node, so a node that keeps rotating cannot grow the table without bound.
REMEMBERED_RETIRED_EPOCHS = 64
DAILY_TABLES: tuple[str, ...] = (
    "CREATE TABLE IF NOT EXISTS traffic_daily (user_id TEXT NOT NULL, day TEXT NOT NULL,"
    " up INTEGER NOT NULL, down INTEGER NOT NULL, PRIMARY KEY (user_id, day));",
    "CREATE TABLE IF NOT EXISTS node_traffic_daily (node_id TEXT NOT NULL, day TEXT NOT NULL,"
    " bytes INTEGER NOT NULL, PRIMARY KEY (node_id, day));",
    "CREATE INDEX IF NOT EXISTS traffic_daily_day ON traffic_daily (day);",
    "CREATE INDEX IF NOT EXISTS node_traffic_daily_day ON node_traffic_daily (day);",
)
NODE_TOTALS_TABLE = """CREATE TABLE IF NOT EXISTS node_traffic_totals (
    node_id TEXT PRIMARY KEY,
    uplink_bytes INTEGER NOT NULL,
    downlink_bytes INTEGER NOT NULL,
    counting_since TEXT NOT NULL,
    active_users INTEGER,
    active_report_at TEXT,
    active_report_received_at TEXT
)"""
TRAFFIC_EVIDENCE_COLUMNS = (
    "ALTER TABLE traffic_totals ADD COLUMN first_traffic_at TEXT",
    "ALTER TABLE traffic_totals ADD COLUMN last_traffic_node TEXT",
)
ACCOUNTING_MIGRATION: tuple[str, ...] = (
    *TRAFFIC_EVIDENCE_COLUMNS,
    NODE_TOTALS_TABLE,
    "INSERT OR IGNORE INTO node_traffic_totals (node_id, uplink_bytes, downlink_bytes, counting_since)"
    " SELECT node_id, SUM(uplink_bytes), SUM(downlink_bytes), strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"
    " FROM traffic_counters GROUP BY node_id",
    # Preserve aggregate node evidence before minimizing orphan per-person records from older deletions.
    *(
        f"DELETE FROM {table} WHERE user_id NOT IN (SELECT user_id FROM users)"
        for table in ("traffic_totals", "traffic_counters", "traffic_daily")
    ),
)


class TrafficLedger:
    def __init__(self, store: Store) -> None:
        self._store = store
        store.create(_SCHEMA)
        if store.meta("schema_version") is None:
            for statement in TRAFFIC_EVIDENCE_COLUMNS:
                store.create(statement)
        for statement in DAILY_TABLES:
            store.create(statement)
        store.create(NODE_TOTALS_TABLE)

    def record(self, node_id: str, report: StatsReport) -> StatsAck:
        """Count one stats report idempotently and acknowledge the highest counted `seq` of its epoch."""
        with self._store.transaction():
            return self.count(node_id, report)

    def count(self, node_id: str, report: StatsReport, *, now: str | None = None) -> StatsAck:
        """`record()` inside a transaction the caller holds."""
        db = self._store.db
        epoch = report.counter_epoch
        last = self._store.row(
            "SELECT seq FROM traffic_reports WHERE node_id = ? AND counter_epoch = ?", (node_id, epoch)
        )
        if last is not None and report.seq <= last["seq"]:
            return StatsAck(counter_epoch=epoch, acked_seq=int(last["seq"]))
        if (
            last is None
            and db.execute(
                "SELECT 1 FROM traffic_retired_epochs WHERE node_id = ? AND counter_epoch = ?", (node_id, epoch)
            ).fetchone()
        ):
            return StatsAck(counter_epoch=epoch, acked_seq=report.seq)
        baselines = {
            str(row["user_id"]): (int(row["uplink_bytes"]), int(row["downlink_bytes"]))
            for row in self._store.rows(
                "SELECT user_id, uplink_bytes, downlink_bytes FROM traffic_counters"
                " WHERE node_id = ? AND counter_epoch = ?",
                (node_id, epoch),
            )
        }
        growth: list[tuple[str, int, int, int, int]] = []
        known = {row["user_id"] for row in self._store.rows("SELECT user_id FROM users")}
        for user in report.users:
            if user.user_id not in known:
                continue
            base_up, base_down = baselines.get(user.user_id, (0, 0))
            up, down = user.uplink_bytes - base_up, user.downlink_bytes - base_down
            if up < 0 or down < 0:
                raise agent_error(
                    "counter_regression", f"Counters of {user.user_id} went down within one counter epoch."
                )
            growth.append((user.user_id, user.uplink_bytes, user.downlink_bytes, up, down))
        now = now or utc_now()
        history = read_privacy(self._store).usage_history == "daily"
        day = history_cutoff(1, now)
        for user_id, total_up, total_down, up, down in growth:
            db.execute(
                "INSERT INTO traffic_counters VALUES (?, ?, ?, ?, ?) ON CONFLICT(node_id, counter_epoch, user_id)"
                " DO UPDATE SET uplink_bytes = excluded.uplink_bytes, downlink_bytes = excluded.downlink_bytes",
                (node_id, epoch, user_id, total_up, total_down),
            )
            if up or down:
                previous = self._store.row("SELECT last_seen_at FROM traffic_totals WHERE user_id = ?", (user_id,))
                recent = (
                    previous is None
                    or previous["last_seen_at"] is None
                    or (datetime.fromisoformat(now) >= datetime.fromisoformat(previous["last_seen_at"]))
                )
                db.execute(
                    "INSERT INTO traffic_totals (user_id, uplink_bytes, downlink_bytes, last_seen_at,"
                    " first_traffic_at, last_traffic_node) VALUES (?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(user_id) DO UPDATE SET"
                    " uplink_bytes = uplink_bytes + excluded.uplink_bytes,"
                    " downlink_bytes = downlink_bytes + excluded.downlink_bytes,"
                    " first_traffic_at = COALESCE(first_traffic_at, excluded.first_traffic_at),"
                    " last_seen_at = CASE WHEN ? THEN excluded.last_seen_at ELSE last_seen_at END,"
                    " last_traffic_node = CASE WHEN ? THEN excluded.last_traffic_node ELSE last_traffic_node END",
                    (user_id, up, down, now, now, node_id, recent, recent),
                )
                if history:
                    db.execute(
                        "INSERT INTO traffic_daily VALUES (?, ?, ?, ?) ON CONFLICT(user_id, day) DO UPDATE SET"
                        " up = up + excluded.up, down = down + excluded.down",
                        (user_id, day, up, down),
                    )
                    db.execute(
                        "INSERT INTO node_traffic_daily VALUES (?, ?, ?) ON CONFLICT(node_id, day) DO UPDATE SET"
                        " bytes = bytes + excluded.bytes",
                        (node_id, day, up + down),
                    )
        db.execute(
            "INSERT INTO node_traffic_totals (node_id, uplink_bytes, downlink_bytes, counting_since)"
            " VALUES (?, ?, ?, ?) ON CONFLICT(node_id) DO UPDATE SET"
            " uplink_bytes = uplink_bytes + excluded.uplink_bytes,"
            " downlink_bytes = downlink_bytes + excluded.downlink_bytes",
            (node_id, sum(item[3] for item in growth), sum(item[4] for item in growth), now),
        )
        self._record_activity(node_id, report, sum(1 for item in growth if item[3] or item[4]), now)
        db.execute(
            "INSERT INTO traffic_reports VALUES (?, ?, ?,"
            " (SELECT COALESCE(MAX(arrival), 0) + 1 FROM traffic_reports WHERE node_id = ?))"
            " ON CONFLICT(node_id, counter_epoch) DO UPDATE SET seq = excluded.seq, arrival = excluded.arrival",
            (node_id, epoch, report.seq, node_id),
        )
        self._retire_beyond_kept(node_id)
        return StatsAck(counter_epoch=epoch, acked_seq=report.seq)

    def forget_user(self, user_id: str) -> None:
        """Erase a removed person's counters in the caller's user-deletion transaction.

        Node report sequences and epochs still deduplicate every other person's growth.
        """
        for table in ("traffic_totals", "traffic_counters", "traffic_daily"):
            self._store.db.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))

    def _retire_beyond_kept(self, node_id: str) -> None:
        db = self._store.db
        stale = [
            str(row["counter_epoch"])
            for row in self._store.rows(
                "SELECT counter_epoch FROM traffic_reports WHERE node_id = ? ORDER BY arrival DESC LIMIT -1 OFFSET ?",
                (node_id, KEPT_EPOCHS),
            )
        ]
        for epoch in stale:
            db.execute("INSERT OR IGNORE INTO traffic_retired_epochs VALUES (?, ?)", (node_id, epoch))
            db.execute("DELETE FROM traffic_counters WHERE node_id = ? AND counter_epoch = ?", (node_id, epoch))
            db.execute("DELETE FROM traffic_reports WHERE node_id = ? AND counter_epoch = ?", (node_id, epoch))
        if stale:
            db.execute(
                "DELETE FROM traffic_retired_epochs WHERE node_id = ? AND rowid NOT IN"
                " (SELECT rowid FROM traffic_retired_epochs WHERE node_id = ? ORDER BY rowid DESC LIMIT ?)",
                (node_id, node_id, REMEMBERED_RETIRED_EPOCHS),
            )

    def node_totals(self) -> dict[str, int]:
        """Durable accepted bytes per node, independent of counter epochs and baseline retention."""
        rows = self._store.rows("SELECT node_id, uplink_bytes + downlink_bytes AS total FROM node_traffic_totals")
        return {str(row["node_id"]): int(row["total"] or 0) for row in rows}

    def node_accounting(self, node_id: str) -> NodeTrafficAccounting:
        row = self._store.row("SELECT * FROM node_traffic_totals WHERE node_id = ?", (node_id,))
        if row is None:
            return NodeTrafficAccounting()
        return NodeTrafficAccounting(
            traffic_bytes=int(row["uplink_bytes"]) + int(row["downlink_bytes"]),
            traffic_counting_since=row["counting_since"],
            active_users=row["active_users"],
            active_report_at=row["active_report_at"],
            active_report_received_at=row["active_report_received_at"],
        )

    def node_evidence(self, node_id: str) -> NodeTrafficEvidence:
        return self.node_accounting(node_id).traffic_evidence()

    def _record_activity(self, node_id: str, report: StatsReport, active: int, now: str) -> None:
        held = self.node_evidence(node_id).active_report_at
        # A delayed epoch can add bytes without replacing a newer collection window.
        if held is None or datetime.fromisoformat(report.collected_at) >= datetime.fromisoformat(held):
            self._store.db.execute(
                "UPDATE node_traffic_totals SET active_users = ?, active_report_at = ?, active_report_received_at = ?"
                " WHERE node_id = ?",
                (active, report.collected_at, now, node_id),
            )

    def summary(self) -> TrafficSummary:
        """Every user's totals across nodes, zero for users no node has counted yet."""
        rows = self._store.rows(
            "SELECT u.username, COALESCE(t.uplink_bytes, 0) AS up, COALESCE(t.downlink_bytes, 0) AS down,"
            " t.last_seen_at FROM users u LEFT JOIN traffic_totals t ON t.user_id = u.user_id ORDER BY u.username"
        )
        return TrafficSummary(
            users=[
                UserTrafficTotal(
                    username=str(row["username"]),
                    uplink_bytes=int(row["up"]),
                    downlink_bytes=int(row["down"]),
                    last_seen_at=row["last_seen_at"],
                )
                for row in rows
            ]
        )

    def prune_history(self, now: str | None = None) -> None:
        """The 30-second timer removes days outside the current cap, including today in that cap."""
        with self._store.transaction():
            settings = read_privacy(self._store)
            if settings.usage_history == "daily":
                prune_history_rows(self._store, settings.history_days, now or utc_now())

    def user_usage(self, username: str, *, days: int = 30, now: str | None = None) -> UserUsage:
        """Per-person growth received on each UTC day; zeros fill gaps, and retention bounds the window."""
        with self._store.lock:
            settings, window = self._history_window(days, now or utc_now())
            user = self._store.row("SELECT user_id FROM users WHERE username = ?", (username,))
            if user is None:
                raise ServiceError("not_found", f"Unknown user {username!r}.", 404)
            rows = self._store.rows(
                "SELECT day, up, down FROM traffic_daily WHERE user_id = ? AND day BETWEEN ? AND ?",
                (user["user_id"], window[0].isoformat(), window[-1].isoformat()),
            )
            totals = {row["day"]: (row["up"], row["down"]) for row in rows}
            return UserUsage(
                username=username,
                days=len(window),
                history_days=settings.history_days,
                daily=[
                    UserTrafficDay(
                        day=day,
                        uplink_bytes=totals.get(day.isoformat(), (0, 0))[0],
                        downlink_bytes=totals.get(day.isoformat(), (0, 0))[1],
                    )
                    for day in window
                ],
            )

    def daily(self, *, days: int = 30, now: str | None = None) -> TrafficDailySummary:
        """Fleet and per-node daily growth, including retained totals of nodes that were removed."""
        with self._store.lock:
            settings, window = self._history_window(days, now or utc_now())
            rows = self._store.rows(
                "SELECT node_id, day, bytes FROM node_traffic_daily WHERE day BETWEEN ? AND ? ORDER BY node_id",
                (window[0].isoformat(), window[-1].isoformat()),
            )
            nodes: dict[str, dict[str, int]] = {}
            fleet: dict[str, int] = {}
            for row in rows:
                nodes.setdefault(row["node_id"], {})[row["day"]] = row["bytes"]
                fleet[row["day"]] = fleet.get(row["day"], 0) + row["bytes"]
            for node in self._store.rows("SELECT node_id FROM nodes"):
                nodes.setdefault(node["node_id"], {})
            return TrafficDailySummary(
                days=len(window),
                history_days=settings.history_days,
                daily=[NodeTrafficDay(day=day, bytes=fleet.get(day.isoformat(), 0)) for day in window],
                nodes=[
                    NodeDailyUsage(
                        node_id=node,
                        daily=[NodeTrafficDay(day=day, bytes=totals.get(day.isoformat(), 0)) for day in window],
                    )
                    for node, totals in sorted(nodes.items())
                ],
            )

    def _history_window(self, days: int, now: str) -> tuple[PrivacySettings, list[date]]:
        settings = read_privacy(self._store)
        if settings.usage_history == "off":
            raise ServiceError(
                "history_off", "Daily usage history is off; enable it with meridian privacy set --history daily.", 409
            )
        if not 1 <= days <= 730:
            raise ServiceError("invalid_request", "days must be between 1 and 730.", 400)
        count = min(days, settings.history_days)
        start = date.fromisoformat(history_cutoff(count, now))
        return settings, [start + timedelta(days=i) for i in range(count)]
