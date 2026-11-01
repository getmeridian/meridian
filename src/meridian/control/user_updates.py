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
        save_user_fields(store, merged)
        enforce(moment)
        compose()
        return read_user(username)


OPERATOR_FIELDS = tuple(dict.fromkeys((*UserUpdate.model_fields, *AccountLifecycleState.model_fields)))


def save_user_fields(store: Store, user: UserRecord, fields: tuple[str, ...] = OPERATOR_FIELDS) -> None:
    """Write validated operator fields in the caller's existing transaction."""
    if not set(fields) <= set(OPERATOR_FIELDS):
        raise ValueError("Only operator-owned fields may be changed.")
    columns = ", ".join(f"{field} = ?" for field in fields)
    store.db.execute(
        f"UPDATE users SET {columns} WHERE user_id = ?", (*(getattr(user, field) for field in fields), user.user_id)
    )
