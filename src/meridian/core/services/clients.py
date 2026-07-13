"""Client service use cases with injectable panel adapters."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol, Self

from pydantic import Field

from meridian.core.clients import (
    ClientListResult,
    ClientShowResult,
    PanelUserLike,
    build_client_list_result,
    build_client_show_result,
)
from meridian.core.models import CoreModel


class ClientNotFoundError(Exception):
    """Raised when a requested client does not exist on the panel."""

    def __init__(self, username: str) -> None:
        self.username = username
        super().__init__(username)


class ClientPanelClient(Protocol):
    """Panel operations needed by client read services."""

    def __enter__(self) -> Self: ...

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> object: ...

    def get_user(self, username: str) -> PanelUserLike | None: ...

    def list_users(self) -> Sequence[PanelUserLike]: ...

    def get_subscription_url(self, short_uuid: str) -> str: ...


ShareUrlBuilder = Callable[[PanelUserLike], str]


class ClientListServiceResult(CoreModel):
    """Client list service result."""

    clients: ClientListResult


class ClientShowServiceResult(CoreModel):
    """Client show service result."""

    client: ClientShowResult
    share_url: str = Field(default="", exclude=True)
    subscription_url: str = Field(default="", exclude=True)


def collect_client_list(panel_client: ClientPanelClient) -> ClientListServiceResult:
    """Collect redacted client list data from the panel."""
    with panel_client as panel:
        users = panel.list_users()
    return ClientListServiceResult(clients=build_client_list_result(users))


def collect_client_show(
    panel_client: ClientPanelClient,
    username: str,
    *,
    build_share_url: ShareUrlBuilder | None = None,
) -> ClientShowServiceResult:
    """Collect one client plus deterministic handoff links."""
    with panel_client as panel:
        user = panel.get_user(username)
        if user is None:
            raise ClientNotFoundError(username)
        share_url = build_share_url(user) if build_share_url else ""
        subscription_url = panel.get_subscription_url(user.short_uuid) if user.short_uuid else ""
    return ClientShowServiceResult(
        client=build_client_show_result(user, share_url=share_url, subscription_url=subscription_url),
        share_url=share_url,
        subscription_url=subscription_url,
    )
