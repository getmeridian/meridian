"""Transactional duration changes to service-owned person settings."""

from __future__ import annotations

import secrets
import uuid as uuid_module
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from meridian.control.models import UserCreate, UserRecord
from meridian.control.store import Store
from meridian.control.user_expiry import require_finite_user_expiry
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
        start = datetime.fromisoformat(moment)
        if user.expire_at is not None:
            start = max(start, datetime.fromisoformat(user.expire_at))
        require_finite_user_expiry(start.astimezone(UTC).isoformat().replace("+00:00", "Z"))
        expiry = (start + timedelta(days=request.days)).astimezone(UTC).isoformat().replace("+00:00", "Z")
        require_finite_user_expiry(expiry)
        store.db.execute("UPDATE users SET expire_at = ? WHERE username = ?", (expiry, username))
        record_update(user, read_user(username))
        enforce(moment)
        compose()
        return read_user(username)


def insert_user(store: Store, create: UserCreate, *, now: Callable[[], str]) -> None:
    """Insert fresh credentials and first settings in the caller's transaction."""
    require_finite_user_expiry(create.expire_at)
    store.db.execute(
        "INSERT INTO users (username, user_id, uuid, sub_token, status, created_at, expire_at,"
        " traffic_limit_bytes, traffic_limit_strategy, device_limit) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            create.username,
            f"u{secrets.token_hex(8)}",
            str(uuid_module.uuid4()),
            secrets.token_urlsafe(18),
            create.status,
            now(),
            create.expire_at,
            create.traffic_limit_bytes,
            create.traffic_limit_strategy,
            create.device_limit,
        ),
    )
