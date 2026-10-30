"""Apply operator person edits inside the service's existing write transaction."""

from __future__ import annotations

from collections.abc import Callable

from meridian.control.events import EventLedger
from meridian.control.models import UserRecord, UserUpdate
from meridian.control.store import Store
from meridian.control.user_expiry import require_finite_user_expiry


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
        changes = update.model_dump(include=update.model_fields_set)
        require_finite_user_expiry(changes.get("expire_at"))
        if "status" in changes and changes["status"] is None:
            del changes["status"]
        for field in ("traffic_limit_bytes", "traffic_limit_strategy"):
            if field in changes and changes[field] is None:
                del changes[field]
        merged = UserRecord.model_validate({**user.model_dump(), **changes})
        events.user_updated(user, merged)
        store.db.execute(
            "UPDATE users SET status = ?, expire_at = ?, traffic_limit_bytes = ?, traffic_limit_strategy = ?,"
            " device_limit = ?, note = ?, tag = ? WHERE username = ?",
            (
                merged.status,
                merged.expire_at,
                merged.traffic_limit_bytes,
                merged.traffic_limit_strategy,
                merged.device_limit,
                merged.note,
                merged.tag,
                username,
            ),
        )
        enforce(now())
        compose()
        return read_user(username)
