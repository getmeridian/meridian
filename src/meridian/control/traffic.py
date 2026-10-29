"""Per-user traffic totals from agents' cumulative `stats` reports.

Each node keeps the last (counter_epoch, seq, per-user totals) it reported;
only growth past those totals is counted, so a resent or older report is
acknowledged without counting and a flaky link never double-counts. A new
counter epoch starts the node's users from zero; an epoch it left is never
counted again. Baselines outlive node deletion: a re-added node whose agent
kept its counter store must not count its totals twice. Nothing here records
where a client connected from.
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
    node_id TEXT PRIMARY KEY,
    counter_epoch TEXT NOT NULL,
    seq INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS traffic_counters (
    node_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    uplink_bytes INTEGER NOT NULL,
    downlink_bytes INTEGER NOT NULL,
    PRIMARY KEY (node_id, user_id)
);
CREATE TABLE IF NOT EXISTS traffic_retired_epochs (
    node_id TEXT NOT NULL,
    counter_epoch TEXT NOT NULL,
    PRIMARY KEY (node_id, counter_epoch)
);
"""


class TrafficLedger:
    def __init__(self, store: Store) -> None:
        self._store = store
        store.create(_SCHEMA)

    def record(self, node_id: str, report: StatsReport) -> StatsAck:
        """Count one stats report idempotently and acknowledge the highest counted `seq`."""
        db = self._store.db
        with self._store.transaction():
            last = self._store.row("SELECT counter_epoch, seq FROM traffic_reports WHERE node_id = ?", (node_id,))
            if last is not None and last["counter_epoch"] == report.counter_epoch:
                if report.seq <= last["seq"]:
                    return StatsAck(counter_epoch=report.counter_epoch, acked_seq=int(last["seq"]))
                baselines = {
                    str(row["user_id"]): (int(row["uplink_bytes"]), int(row["downlink_bytes"]))
                    for row in self._store.rows(
                        "SELECT user_id, uplink_bytes, downlink_bytes FROM traffic_counters WHERE node_id = ?",
                        (node_id,),
                    )
                }
            else:
                retired = db.execute(
                    "SELECT 1 FROM traffic_retired_epochs WHERE node_id = ? AND counter_epoch = ?",
                    (node_id, report.counter_epoch),
                ).fetchone()
                if retired:
                    return StatsAck(counter_epoch=report.counter_epoch, acked_seq=report.seq)
                if last is not None:
                    db.execute(
                        "INSERT OR IGNORE INTO traffic_retired_epochs VALUES (?, ?)", (node_id, last["counter_epoch"])
                    )
                db.execute("DELETE FROM traffic_counters WHERE node_id = ?", (node_id,))
                baselines = {}
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
                    "INSERT INTO traffic_counters VALUES (?, ?, ?, ?) ON CONFLICT(node_id, user_id) DO UPDATE SET"
                    " uplink_bytes = excluded.uplink_bytes, downlink_bytes = excluded.downlink_bytes",
                    (node_id, user_id, total_up, total_down),
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
                "INSERT INTO traffic_reports VALUES (?, ?, ?) ON CONFLICT(node_id) DO UPDATE SET"
                " counter_epoch = excluded.counter_epoch, seq = excluded.seq",
                (node_id, report.counter_epoch, report.seq),
            )
        return StatsAck(counter_epoch=report.counter_epoch, acked_seq=report.seq)

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
