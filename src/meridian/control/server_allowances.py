"""Reviewed server settings and durable UTC allowance offsets over accepted node totals."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import TypeAdapter

from meridian.control.store import Store
from meridian.core.inputs import ServerReferenceValue
from meridian.core.server_allowances import (
    APPROXIMATE_TRAFFIC_COVERAGE,
    UNMEASURED_TRAFFIC_COVERAGE,
    ServerAllowanceSpec,
    ServerUsage,
    ServerUsageList,
)
from meridian.core.server_calendar import next_payment_date, server_period

ALLOWANCE_PERIODS_TABLE = """CREATE TABLE IF NOT EXISTS server_allowance_periods (
    server_ref TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    reset_day INTEGER NOT NULL,
    period_start TEXT NOT NULL,
    offset_bytes INTEGER NOT NULL,
    counting_since TEXT NOT NULL
)"""
_SPECS = TypeAdapter(dict[ServerReferenceValue, ServerAllowanceSpec])


def server_settings(store: Store) -> dict[str, ServerAllowanceSpec]:
    return _SPECS.validate_json(store.meta("server_settings") or "{}")


def _total(store: Store, node_id: str) -> int:
    row = store.row("SELECT uplink_bytes, downlink_bytes FROM node_traffic_totals WHERE node_id = ?", (node_id,))
    return int(row["uplink_bytes"]) + int(row["downlink_bytes"]) if row is not None else 0


def _timestamp(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def save_server_settings(store: Store, specs: dict[str, ServerAllowanceSpec], now: str) -> None:
    """Save metadata and any new accounting anchors in the caller's settings transaction."""
    moment = datetime.fromisoformat(now)
    for server_ref, spec in specs.items():
        if spec.node_id is None or spec.metadata.allowance_bytes is None:
            continue
        start, _end = server_period(spec.metadata.reset_day, moment)
        row = store.row("SELECT * FROM server_allowance_periods WHERE server_ref = ?", (server_ref,))
        if row is None or row["node_id"] != spec.node_id or row["reset_day"] != spec.metadata.reset_day:
            store.db.execute(
                "INSERT INTO server_allowance_periods VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(server_ref) DO UPDATE SET"
                " node_id = excluded.node_id, reset_day = excluded.reset_day, period_start = excluded.period_start,"
                " offset_bytes = excluded.offset_bytes, counting_since = excluded.counting_since",
                (
                    server_ref,
                    spec.node_id,
                    spec.metadata.reset_day,
                    _timestamp(start),
                    _total(store, spec.node_id),
                    now,
                ),
            )
    tracked = {
        ref for ref, spec in specs.items() if spec.node_id is not None and spec.metadata.allowance_bytes is not None
    }
    for row in store.rows("SELECT server_ref FROM server_allowance_periods"):
        if row["server_ref"] not in tracked:
            store.db.execute("DELETE FROM server_allowance_periods WHERE server_ref = ?", (row["server_ref"],))
    store.set_meta("server_settings", _SPECS.dump_json(specs).decode("utf-8"))
    advance_server_periods(store, now)


def advance_server_periods(store: Store, now: str) -> None:
    """Advance offsets before accepted growth, within the traffic or enforcement transaction."""
    moment = datetime.fromisoformat(now)
    for row in store.rows("SELECT * FROM server_allowance_periods"):
        start, _end = server_period(int(row["reset_day"]), moment)
        if datetime.fromisoformat(row["period_start"]) < start:
            store.db.execute(
                "UPDATE server_allowance_periods SET period_start = ?, offset_bytes = ? WHERE server_ref = ?",
                (_timestamp(start), _total(store, row["node_id"]), row["server_ref"]),
            )


def read_server_usage(store: Store, now: str) -> ServerUsageList:
    """A consistent read; elapsed periods and recurring payment dates never write on read."""
    moment = datetime.fromisoformat(now).astimezone(UTC)
    with store.lock:
        servers: list[ServerUsage] = []
        for server_ref, spec in sorted(server_settings(store).items()):
            measured = spec.node_id is not None
            total = _total(store, spec.node_id) if spec.node_id is not None else None
            start, end = server_period(spec.metadata.reset_day, moment)
            row = store.row("SELECT * FROM server_allowance_periods WHERE server_ref = ?", (server_ref,))
            used: int | None = None
            counting_since: str | None = None
            if measured and spec.metadata.allowance_bytes is not None:
                used = 0
                counting_since = _timestamp(start)
                if row is not None and datetime.fromisoformat(row["period_start"]) == start:
                    used = max(0, (total or 0) - int(row["offset_bytes"]))
                    counting_since = _timestamp(max(start, datetime.fromisoformat(row["counting_since"])))
            servers.append(
                ServerUsage(
                    server_ref=server_ref,
                    node_id=spec.node_id,
                    metadata=spec.metadata,
                    measurement="accepted_user_traffic" if measured else "not_measured",
                    coverage=APPROXIMATE_TRAFFIC_COVERAGE if measured else UNMEASURED_TRAFFIC_COVERAGE,
                    used_bytes=used,
                    lifetime_bytes=total,
                    period_start=_timestamp(start) if used is not None else None,
                    period_end=_timestamp(end) if used is not None else None,
                    counting_since=counting_since,
                    next_payment_on=next_payment_date(spec.metadata.pay_on, spec.metadata.pay_every, moment.date()),
                )
            )
        return ServerUsageList(observed_at=now, servers=servers)
