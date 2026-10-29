"""Per-user traffic totals from agents' cumulative `stats` reports.

Each node keeps, per counter epoch, the last seq and per-user totals it
reported; only growth past those totals is counted, so a resent or older
report is acknowledged without counting and a flaky link never double-counts.
A new counter epoch starts from zero beside the others: epoch IDs are random,
so a late report from an epoch the agent left (or one never seen) is counted
against its own baselines and never retires the live one. Only the
`KEPT_EPOCHS` most recently reported epochs keep baselines; older ones are
retired and never count again. Baselines outlive node deletion: a re-added
node whose agent kept its counter store must not count its totals twice.
Nothing here records where a client connected from.
"""

from __future__ import annotations

from meridian.control.errors import agent_error
from meridian.control.models import TrafficSummary, UserTrafficTotal
from meridian.control.store import Store
from meridian.control.times import utc_now
from meridian.core.agent_protocol import StatsAck, StatsReport

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


class TrafficLedger:
    def __init__(self, store: Store) -> None:
        self._store = store
        store.create(_SCHEMA)

    def record(self, node_id: str, report: StatsReport) -> StatsAck:
        """Count one stats report idempotently and acknowledge the highest counted `seq` of its epoch."""
        with self._store.transaction():
            return self.count(node_id, report)

    def count(self, node_id: str, report: StatsReport) -> StatsAck:
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
        for user in report.users:
            base_up, base_down = baselines.get(user.user_id, (0, 0))
            up, down = user.uplink_bytes - base_up, user.downlink_bytes - base_down
            if up < 0 or down < 0:
                raise agent_error(
                    "counter_regression", f"Counters of {user.user_id} went down within one counter epoch."
                )
            growth.append((user.user_id, user.uplink_bytes, user.downlink_bytes, up, down))
        now = utc_now()
        for user_id, total_up, total_down, up, down in growth:
            db.execute(
                "INSERT INTO traffic_counters VALUES (?, ?, ?, ?, ?) ON CONFLICT(node_id, counter_epoch, user_id)"
                " DO UPDATE SET uplink_bytes = excluded.uplink_bytes, downlink_bytes = excluded.downlink_bytes",
                (node_id, epoch, user_id, total_up, total_down),
            )
            if up or down:
                db.execute(
                    "INSERT INTO traffic_totals VALUES (?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET"
                    " uplink_bytes = uplink_bytes + excluded.uplink_bytes,"
                    " downlink_bytes = downlink_bytes + excluded.downlink_bytes,"
                    " last_seen_at = excluded.last_seen_at",
                    (user_id, up, down, now),
                )
        db.execute(
            "INSERT INTO traffic_reports VALUES (?, ?, ?,"
            " (SELECT COALESCE(MAX(arrival), 0) + 1 FROM traffic_reports WHERE node_id = ?))"
            " ON CONFLICT(node_id, counter_epoch) DO UPDATE SET seq = excluded.seq, arrival = excluded.arrival",
            (node_id, epoch, report.seq, node_id),
        )
        self._retire_beyond_kept(node_id)
        return StatsAck(counter_epoch=epoch, acked_seq=report.seq)

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

    def node_totals(self) -> dict[str, int]:
        """Bytes each node counted in its current counter epoch (an agent that lost its counter store starts over)."""
        rows = self._store.rows(
            "SELECT c.node_id, SUM(c.uplink_bytes + c.downlink_bytes) AS total FROM traffic_counters c"
            " JOIN traffic_reports r ON r.node_id = c.node_id AND r.counter_epoch = c.counter_epoch"
            " WHERE r.arrival = (SELECT MAX(arrival) FROM traffic_reports WHERE node_id = c.node_id)"
            " GROUP BY c.node_id"
        )
        return {str(row["node_id"]): int(row["total"] or 0) for row in rows}

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
