"""Meridian's control service behind the panel surface that client commands, pages, and tests use.

`client` commands, connection pages, and `meridian test` read and change users
through a small panel subset (`get_user`, `list_users`, `fetch_subscription`,
`enable_user`, `disable_user`, `revoke_user_subscription`). This adapter answers
it from the control service with Remnawave's types and errors, so those paths
keep one implementation until the panel is removed (#95).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import TypeVar

from meridian.cluster import ClusterConfig
from meridian.control.client import ControlClient, ControlServiceAuthError, ControlServiceError
from meridian.control.models import UserRecord, UserUpdate
from meridian.remnawave import (
    XRAY_JSON_CLIENT_TYPE,
    MeridianPanel,
    RemnawaveAuthError,
    RemnawaveError,
    RemnawaveNetworkError,
    SubscriptionDocument,
    User,
)

T = TypeVar("T")


class ServiceAccessPanel:
    """The user-facing subset of `MeridianPanel`, served by the control service."""

    def __init__(self, url: str, token: str, timeout: float = 20.0) -> None:
        self._client = ControlClient(url, token, timeout=max(1, math.ceil(timeout)))
        # Panel calls name users by ID; the service names them by username.
        self._usernames: dict[str, str] = {}

    def __enter__(self) -> ServiceAccessPanel:
        return self

    def __exit__(self, *args: object) -> None:
        self._client.close()

    def get_user(self, username: str) -> User | None:
        record = _translated(lambda: self._client.get_user(username))
        return self._user(record) if record is not None else None

    def list_users(self) -> list[User]:
        return [self._user(record) for record in _translated(self._client.list_users)]

    def fetch_subscription(self, short_uuid: str, *, client_type: str = "") -> SubscriptionDocument:
        document = "json" if client_type == XRAY_JSON_CLIENT_TYPE else ""
        content = _translated(lambda: self._client.fetch_subscription(short_uuid, document=document))
        return SubscriptionDocument(url=f"sub/{short_uuid}", content=content, client_type=client_type)

    def enable_user(self, user_id: str) -> None:
        self._update(user_id, UserUpdate(status="active"))

    def disable_user(self, user_id: str) -> None:
        self._update(user_id, UserUpdate(status="disabled"))

    def revoke_user_subscription(self, user_id: str) -> User:
        username = self._username(user_id)
        return self._user(_translated(lambda: self._client.reissue_user(username)))

    def _update(self, user_id: str, update: UserUpdate) -> None:
        username = self._username(user_id)
        _translated(lambda: self._client.update_user(username, update))

    def _username(self, user_id: str) -> str:
        username = self._usernames.get(user_id)
        if username is None:
            # Every caller reads the user first; an unknown ID is a programming error, not a missing user.
            raise RemnawaveError(f"User {user_id} was not read through this connection.", category="bug")
        return username

    def _user(self, record: UserRecord) -> User:
        self._usernames[record.user_id] = record.username
        return User(
            id=record.user_id,
            short_uuid=record.sub_token,
            username=record.username,
            vless_uuid=record.uuid,
            status=record.status.upper(),
            traffic_limit_bytes=record.traffic_limit_bytes,
            created_at=record.created_at,
        )


AccessPanel = MeridianPanel | ServiceAccessPanel


def access_panel(cluster: ClusterConfig) -> AccessPanel:
    """The fleet's user directory: Remnawave, or Meridian's own control service."""
    if cluster.runs_control_service:
        return ServiceAccessPanel(cluster.panel.url, cluster.panel.api_token)
    return MeridianPanel(cluster.panel.url, cluster.panel.api_token)


def _translated(call: Callable[[], T]) -> T:
    try:
        return call()
    except ControlServiceAuthError as exc:
        raise RemnawaveAuthError(str(exc), hint=exc.hint, category="user") from exc
    except ControlServiceError as exc:
        if exc.retryable:
            raise RemnawaveNetworkError(str(exc), hint=exc.hint, category="system") from exc
        raise RemnawaveError(str(exc), hint=exc.hint, category=exc.category) from exc
