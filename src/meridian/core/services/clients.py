"""Client service use cases with injectable panel adapters."""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from typing import Protocol, Self

from pydantic import Field

from meridian.control.models import UserUsage
from meridian.core.attention import AttentionList
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


ShareUrlBuilder = Callable[[PanelUserLike], str]
SubscriptionUrlBuilder = Callable[[str], str]


class ClientListServiceResult(CoreModel):
    """Client list service result."""

    clients: ClientListResult


class ClientShowServiceResult(CoreModel):
    """Client show service result."""

    client: ClientShowResult
    share_url: str = Field(default="", exclude=True)
    subscription_url: str = Field(default="", exclude=True)


def collect_client_list(
    panel_client: ClientPanelClient,
    *,
    allowed_usernames: Collection[str] | None = None,
    declared_usernames: Collection[str] | None = None,
    read_attention: Callable[[], AttentionList] | None = None,
    tag: str | None = None,
) -> ClientListServiceResult:
    """Collect redacted client list data from the panel; `declared_usernames` marks the rest undeclared."""
    with panel_client as panel:
        users = panel.list_users()
        attention = {person.username: person.reasons for person in read_attention().people} if read_attention else None
    if allowed_usernames is not None:
        allowed = set(allowed_usernames)
        users = [user for user in users if user.username in allowed]
    if tag is not None:
        users = [user for user in users if getattr(user, "tag", "") == tag]
    declared = None if declared_usernames is None else set(declared_usernames)
    return ClientListServiceResult(clients=build_client_list_result(users, declared=declared, attention=attention))


def collect_client_show(
    panel_client: ClientPanelClient,
    username: str,
    *,
    build_share_url: ShareUrlBuilder | None = None,
    build_subscription_url: SubscriptionUrlBuilder | None = None,
    allowed_usernames: Collection[str] | None = None,
    declared_usernames: Collection[str] | None = None,
    read_usage: Callable[[str], UserUsage | None] | None = None,
) -> ClientShowServiceResult:
    """Collect one client plus deterministic handoff links."""
    if allowed_usernames is not None and username not in allowed_usernames:
        raise ClientNotFoundError(username)
    with panel_client as panel:
        user = panel.get_user(username)
        if user is None:
            raise ClientNotFoundError(username)
        share_url = build_share_url(user) if build_share_url else ""
        subscription_url = build_subscription_url(user.short_uuid) if build_subscription_url and user.short_uuid else ""
        usage = read_usage(username) if read_usage else None
    return ClientShowServiceResult(
        client=build_client_show_result(
            user,
            share_url=share_url,
            subscription_url=subscription_url,
            declared=declared_usernames is None or username in declared_usernames,
            usage=usage,
        ),
        share_url=share_url,
        subscription_url=subscription_url,
    )
