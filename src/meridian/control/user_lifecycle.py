"""Transactional duration changes to service-owned person settings."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from meridian.control.errors import ServiceError
from meridian.control.models import UserRecord
from meridian.control.store import Store
from meridian.core.client_limits import ClientExtendRequest


def extend_user_expiry(
    store: Store,
    username: str,
    *,
    days: int,
    now: Callable[[], str],
    read_user: Callable[[str], UserRecord],
    enforce: Callable[[str], object],
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
        try:
            expiry = (start + timedelta(days=request.days)).astimezone(UTC).isoformat().replace("+00:00", "Z")
        except OverflowError as exc:
            raise ServiceError("invalid_request", "The extended expiry would be beyond year 9999.", 400) from exc
        store.db.execute("UPDATE users SET expire_at = ? WHERE username = ?", (expiry, username))
        enforce(moment)
        compose()
        return read_user(username)
