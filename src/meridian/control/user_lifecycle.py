"""Transactional duration changes to service-owned person settings."""

from __future__ import annotations

import secrets
import uuid as uuid_module
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from meridian.control.account_lifecycle import validate_user_schedule
from meridian.control.errors import ServiceError
from meridian.control.models import UserCreate, UserRecord
from meridian.control.store import Store
from meridian.control.user_expiry import require_finite_user_expiry
from meridian.control.users import USER_FIELDS
from meridian.core.client_limits import ClientExtendRequest


def extend_user_expiry(
    store: Store,
    username: str,
    *,
    days: int,
    now: Callable[[], str],
    read_user: Callable[[str], UserRecord],
    enforce: Callable[[str], object],
    record_update: Callable[[UserRecord, UserRecord], None],
    compose: Callable[[], None],
) -> UserRecord:
    """Compute, enforce, and return the extension while holding the service's write transaction."""
    request = ClientExtendRequest(days=days)
    with store.transaction():
        user = read_user(username)
        moment = now()
        if user.expire_at is None and user.activation_duration_days is not None and user.first_traffic_at is None:
            duration = user.activation_duration_days + request.days
            if duration > 3650:
                raise ServiceError("invalid_request", "Duration cannot exceed 3650 days from first use.", 400)
            store.db.execute("UPDATE users SET activation_duration_days = ? WHERE username = ?", (duration, username))
            record_update(user, read_user(username))
            enforce(moment)
            compose()
            return read_user(username)
        start = datetime.fromisoformat(moment)
        if user.expire_at is not None:
            start = max(start, datetime.fromisoformat(user.expire_at))
        require_finite_user_expiry(start.astimezone(UTC).isoformat().replace("+00:00", "Z"))
        expiry = (start + timedelta(days=request.days)).astimezone(UTC).isoformat().replace("+00:00", "Z")
        require_finite_user_expiry(expiry)
        store.db.execute(
            "UPDATE users SET expire_at = ?, renewals_completed = 0, renewal_anchor_at = ? WHERE username = ?",
            (expiry, expiry if user.renewal_enabled else None, username),
        )
        record_update(user, read_user(username))
        enforce(moment)
        compose()
        return read_user(username)


def insert_user(store: Store, create: UserCreate, *, now: Callable[[], str]) -> None:
    """Insert fresh credentials and first settings in the caller's transaction."""
    moment = now()
    initial = UserRecord.model_validate(
        {
            **create.model_dump(),
            "user_id": f"u{secrets.token_hex(8)}",
            "uuid": str(uuid_module.uuid4()),
            "sub_token": secrets.token_urlsafe(18),
            "created_at": moment,
            "traffic_reset_anchor_day": create.traffic_reset_anchor_day
            or datetime.fromisoformat(moment).astimezone(UTC).day,
            "renewal_anchor_at": create.expire_at if create.renewal_enabled else None,
        }
    )
    validate_user_schedule(initial, moment)
    columns = ", ".join(USER_FIELDS)
    placeholders = ", ".join("?" for _field in USER_FIELDS)
    store.db.execute(
        f"INSERT INTO users ({columns}) VALUES ({placeholders})",
        tuple(getattr(initial, field) for field in USER_FIELDS),
    )
