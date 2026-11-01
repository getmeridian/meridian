"""Apply operator person edits inside the service's existing write transaction."""

from __future__ import annotations

from collections.abc import Callable

from meridian.control.account_lifecycle import merge_user_update
from meridian.control.events import EventLedger
from meridian.control.models import UserRecord, UserUpdate
from meridian.control.store import Store
from meridian.core.account_schedule import AccountLifecycleState


def update_user(
    store: Store,
    username: str,
    update: UserUpdate,
    *,
    read_user: Callable[[str], UserRecord],
    events: EventLedger,
    now: Callable[[], str],
    enforce: Callable[[str], object],
    compose: Callable[[], object],
) -> UserRecord:
    """Preserve omitted fields, record the edit, enforce limits, and compose atomically."""
    with store.transaction():
        user = read_user(username)
        moment = now()
        merged = merge_user_update(user, update, moment)
        events.user_updated(user, merged)
        schedule_fields = tuple(AccountLifecycleState.model_fields)
        schedule_sql = ", ".join(f"{field} = ?" for field in schedule_fields)
        store.db.execute(
            "UPDATE users SET status = ?, expire_at = ?, traffic_limit_bytes = ?, traffic_limit_strategy = ?,"
            f" device_limit = ?, note = ?, tag = ?, language = ?, {schedule_sql} WHERE username = ?",
            (
                merged.status,
                merged.expire_at,
                merged.traffic_limit_bytes,
                merged.traffic_limit_strategy,
                merged.device_limit,
                merged.note,
                merged.tag,
                merged.language,
                *(getattr(merged, field) for field in schedule_fields),
                username,
            ),
        )
        enforce(moment)
        compose()
        return read_user(username)
