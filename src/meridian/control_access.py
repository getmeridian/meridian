"""Read Meridian's control service through the panel surface client verification uses.

`connectivity.py` selects a client and fetches its Xray JSON subscription from
whichever control backend the fleet runs; this adapter answers those calls
from the control service with Remnawave's types and errors, so verification
keeps one code path until the panel is gone (#95).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from types import TracebackType
from typing import TypeVar

from meridian.control.client import ControlClient, ControlServiceAuthError, ControlServiceError
from meridian.control.models import UserRecord
from meridian.remnawave import (
    XRAY_JSON_CLIENT_TYPE,
    RemnawaveAuthError,
    RemnawaveError,
    RemnawaveNetworkError,
    SubscriptionDocument,
    User,
)

T = TypeVar("T")


class ServiceVerificationPanel:
    """The `get_user`/`list_users`/`fetch_subscription` subset of `MeridianPanel`."""

    def __init__(self, url: str, token: str, timeout: float) -> None:
        self._client = ControlClient(url, token, timeout=max(1, math.ceil(timeout)))

    def __enter__(self) -> ServiceVerificationPanel:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self._client.close()

    def get_user(self, username: str) -> User | None:
        record = _translated(lambda: self._client.get_user(username))
        return _user(record) if record is not None else None

    def list_users(self) -> list[User]:
        return [_user(record) for record in _translated(self._client.list_users)]

    def fetch_subscription(self, short_uuid: str, *, client_type: str = "") -> SubscriptionDocument:
        document = "json" if client_type == XRAY_JSON_CLIENT_TYPE else ""
        content = _translated(lambda: self._client.fetch_subscription(short_uuid, document=document))
        return SubscriptionDocument(url=f"sub/{short_uuid}", content=content, client_type=client_type)


def _translated(call: Callable[[], T]) -> T:
    try:
        return call()
    except ControlServiceAuthError as exc:
        raise RemnawaveAuthError(str(exc), hint=exc.hint, category="user") from exc
    except ControlServiceError as exc:
        if exc.retryable:
            raise RemnawaveNetworkError(str(exc), hint=exc.hint, category="system") from exc
        raise RemnawaveError(str(exc), hint=exc.hint, category=exc.category) from exc


def _user(record: UserRecord) -> User:
    return User(
        id=record.user_id,
        short_uuid=record.sub_token,
        username=record.username,
        vless_uuid=record.uuid,
        status=record.status.upper(),
        created_at=record.created_at,
    )
