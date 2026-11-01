"""One transactional credential replacement for rotation and subscription reissue."""

import secrets
import uuid
from collections.abc import Callable

from meridian.control.devices import DeviceLedger
from meridian.control.errors import ServiceError
from meridian.control.events import EventLedger
from meridian.control.models import UserRecord
from meridian.control.store import Store
from meridian.core.agent_types import MAX_SAFE_INTEGER


def replace_credentials(
    store: Store,
    username: str,
    *,
    keep_subscription: bool,
    expected_generation: int | None,
    read_user: Callable[[str], UserRecord],
    devices: DeviceLedger,
    events: EventLedger,
    compose: Callable[[], None],
) -> UserRecord:
    with store.transaction():
        before = read_user(username)
        if expected_generation is not None and before.credential_generation != expected_generation:
            raise ServiceError(
                "credentials_changed", "The connection credentials changed. Refresh the person and try again.", 409
            )
        if before.credential_generation >= MAX_SAFE_INTEGER:
            raise ServiceError("conflict", "The credential generation limit has been reached.", 409)
        token = before.sub_token if keep_subscription else secrets.token_urlsafe(18)
        store.db.execute(
            "UPDATE users SET uuid = ?, sub_token = ?, credential_generation = ? WHERE username = ?",
            (str(uuid.uuid4()), token, before.credential_generation + 1, username),
        )
        if not keep_subscription:
            devices.remove(before.user_id)
        events.append("user.credentials_rotated" if keep_subscription else "user.reissued", username)
        compose()
        return read_user(username)
