"""Transactional credential, traffic, and schedule imports from another control plane."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import UTC, datetime

from meridian.control.account_lifecycle import validate_user_schedule
from meridian.control.errors import ServiceError
from meridian.control.events import EventLedger
from meridian.control.models import UserImportBatch, UserImportResult, UserRecord
from meridian.control.period_usage import import_period
from meridian.control.store import Store
from meridian.control.user_expiry import require_finite_user_expiry
from meridian.control.user_policy import period_began, whole_seconds
from meridian.control.users import USER_FIELDS


def import_users(
    store: Store,
    batch: UserImportBatch,
    *,
    read_user: Callable[[str], UserRecord | None],
    now: Callable[[], str],
    enforce: Callable[[str], object],
    compose: Callable[[], object],
    events: EventLedger,
) -> UserImportResult:
    """Keep the first import, or replace atomically while preserving counted lifetime use."""
    created: list[str] = []
    unchanged: list[str] = []
    replaced: list[str] = []
    with store.transaction():
        moment = now()
        for item in batch.users:
            existing = read_user(item.username)
            if existing is not None and not batch.replace:
                if (existing.uuid.lower(), existing.sub_token) != (item.uuid.lower(), item.sub_token):
                    raise ServiceError(
                        "conflict", f"User {item.username!r} already exists with other credentials.", 409
                    )
                unchanged.append(item.username)
                continue
            holder = store.row(
                "SELECT username FROM users WHERE (lower(uuid) = ? OR sub_token = ?) AND username != ?",
                (item.uuid.lower(), item.sub_token, item.username),
            )
            if holder is not None:
                raise ServiceError(
                    "conflict", f"User {item.username!r} has credentials that {holder[0]!r} already holds.", 409
                )
            carried = max(item.lifetime_traffic_bytes, item.used_traffic_bytes)
            counted = 0
            if existing is not None:
                row = store.row("SELECT traffic_carried_bytes FROM users WHERE username = ?", (item.username,))
                assert row is not None
                counted = existing.lifetime_traffic_bytes - row[0]
            created_at = whole_seconds(item.created_at) or moment
            record = UserRecord.model_validate(
                {
                    **item.model_dump(exclude={"created_at"}),
                    "user_id": existing.user_id if existing is not None else f"u{secrets.token_hex(8)}",
                    "created_at": created_at,
                    "language": existing.language if existing is not None else None,
                    "expire_at": whole_seconds(require_finite_user_expiry(item.expire_at)),
                    "traffic_reset_at": period_began(item, moment),
                    "traffic_reset_anchor_day": item.traffic_reset_anchor_day
                    or datetime.fromisoformat(created_at).astimezone(UTC).day,
                    "renewal_anchor_at": item.renewal_anchor_at
                    or (item.expire_at if item.renewal_enabled and item.renewals_completed == 0 else None),
                    "lifetime_traffic_bytes": counted + carried,
                    # A replacing import keeps the first use the service already counted.
                    "first_traffic_at": item.first_traffic_at
                    or (existing.first_traffic_at if existing is not None else None),
                }
            )
            validate_user_schedule(record, moment)
            columns = (*USER_FIELDS, "traffic_carried_bytes")
            values = tuple(getattr(record, field) for field in USER_FIELDS) + (carried,)
            if existing is None:
                placeholders = ", ".join("?" for _column in columns)
                store.db.execute(f"INSERT INTO users ({', '.join(columns)}) VALUES ({placeholders})", values)
                created.append(item.username)
                events.append("user.created", item.username)
            else:
                updates = ", ".join(f"{field} = ?" for field in columns)
                store.db.execute(f"UPDATE users SET {updates} WHERE username = ?", (*values, item.username))
                replaced.append(item.username)
            if item.first_traffic_at is not None:
                store.db.execute(
                    "INSERT INTO traffic_totals (user_id, uplink_bytes, downlink_bytes, first_traffic_at)"
                    " VALUES (?, 0, 0, ?) ON CONFLICT(user_id) DO UPDATE SET"
                    " first_traffic_at = COALESCE(first_traffic_at, excluded.first_traffic_at)",
                    (record.user_id, whole_seconds(item.first_traffic_at)),
                )
            import_period(store, item.username, item.used_traffic_bytes)
            if existing is not None:
                after = read_user(item.username)
                assert after is not None
                events.user_updated(existing, after)
        enforce(moment)
        compose()
    return UserImportResult(created=created, unchanged=unchanged, replaced=replaced)
