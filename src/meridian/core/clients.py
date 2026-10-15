"""Client API result models for meridian-core."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Protocol, Sequence

from pydantic import Field

from meridian.core.models import CoreModel


class PanelUserLike(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def short_uuid(self) -> str: ...

    @property
    def username(self) -> str: ...

    @property
    def vless_uuid(self) -> str: ...

    @property
    def status(self) -> str: ...

    @property
    def used_traffic_bytes(self) -> int: ...

    @property
    def traffic_limit_bytes(self) -> int: ...

    @property
    def created_at(self) -> str: ...

    @property
    def online_at(self) -> str: ...


class ClientRecord(CoreModel):
    """Redacted client metadata suitable for list views."""

    username: str
    id: str
    status: str
    traffic_used_bytes: int
    traffic_limit_bytes: int
    created_at: str
    last_seen: str


class ClientHandoff(CoreModel):
    """Handoff links for one client: a page to open and a subscription to import."""

    share_available: bool
    subscription_available: bool
    share_url: str = ""
    subscription_url: str = ""


class ClientDetail(ClientRecord):
    """Client metadata for a detail view."""


class ClientListSummary(CoreModel):
    """Aggregate client counts for dashboards."""

    clients: int
    active: int
    disabled: int
    limited: int
    expired: int
    other: int

    @property
    def text(self) -> str:
        suffix = "s" if self.clients != 1 else ""
        return f"{self.clients} client{suffix}"


class ClientListResult(CoreModel):
    """Result for listing clients."""

    summary: ClientListSummary
    clients: list[ClientRecord] = Field(default_factory=list)


class ClientShowResult(CoreModel):
    """Result for showing one client."""

    client: ClientDetail
    handoff: ClientHandoff


class ClientCreated(CoreModel):
    """Client metadata returned after creation or reissue, with their handoff links."""

    username: str
    id: str
    status: str
    page_url: str = ""
    subscription_url: str = ""


class ClientAddResult(CoreModel):
    """Result for adding one or more clients."""

    clients: list[ClientCreated] = Field(default_factory=list)


class ClientReissueResult(CoreModel):
    """Result for reissuing one client's links; the old ones no longer work."""

    client: ClientCreated


class ClientReference(CoreModel):
    """Public identity of a client mutation target."""

    username: str


class ClientRemoveResult(CoreModel):
    """Result for removing one client."""

    client: ClientReference


ClientMutationStatus = Literal["active", "disabled"]


class ClientStatus(CoreModel):
    """Client identity and status returned after a status mutation."""

    username: str
    status: ClientMutationStatus


class ClientStatusResult(CoreModel):
    """Result for enabling or disabling one client."""

    client: ClientStatus


def build_client_record(user: PanelUserLike) -> ClientRecord:
    """Build a redacted client record from a panel user object."""
    return ClientRecord(
        username=user.username,
        id=user.id,
        status=user.status,
        traffic_used_bytes=user.used_traffic_bytes,
        traffic_limit_bytes=user.traffic_limit_bytes,
        created_at=user.created_at,
        last_seen=user.online_at,
    )


def build_client_detail(user: PanelUserLike) -> ClientDetail:
    """Build one client detail result from panel state."""
    base = build_client_record(user)
    return ClientDetail(**base.model_dump())


def build_client_list_result(users: Sequence[PanelUserLike]) -> ClientListResult:
    """Build a client list result with status counts."""
    records = [build_client_record(user) for user in users]
    statuses = [record.status.upper() for record in records]
    active = sum(1 for status in statuses if status == "ACTIVE")
    disabled = sum(1 for status in statuses if status == "DISABLED")
    limited = sum(1 for status in statuses if status == "LIMITED")
    expired = sum(1 for status in statuses if status == "EXPIRED")
    other = len(statuses) - active - disabled - limited - expired
    return ClientListResult(
        summary=ClientListSummary(
            clients=len(records),
            active=active,
            disabled=disabled,
            limited=limited,
            expired=expired,
            other=other,
        ),
        clients=records,
    )


def build_client_show_result(
    user: PanelUserLike,
    *,
    share_url: str = "",
    subscription_url: str = "",
) -> ClientShowResult:
    """Build a one-client result with optional handoff links."""
    return ClientShowResult(
        client=build_client_detail(user),
        handoff=ClientHandoff(
            share_available=bool(share_url),
            subscription_available=bool(subscription_url),
            share_url=share_url,
            subscription_url=subscription_url,
        ),
    )


def build_client_add_result(
    users: Sequence[tuple[str, PanelUserLike]],
    links: Mapping[str, tuple[str, str]] | None = None,
) -> ClientAddResult:
    """Build the result for a client add batch; ``links`` maps username to (page, subscription)."""
    known = links or {}
    return ClientAddResult(
        clients=[
            ClientCreated(
                username=username,
                id=user.id,
                status=user.status.lower(),
                page_url=known.get(username, ("", ""))[0],
                subscription_url=known.get(username, ("", ""))[1],
            )
            for username, user in users
        ]
    )


def build_client_reissue_result(user: PanelUserLike, *, page_url: str, subscription_url: str) -> ClientReissueResult:
    """Build the result returned after reissuing one client's links."""
    return ClientReissueResult(
        client=ClientCreated(
            username=user.username,
            id=user.id,
            status=user.status.lower(),
            page_url=page_url,
            subscription_url=subscription_url,
        )
    )


def build_client_remove_result(username: str) -> ClientRemoveResult:
    """Build the stable result returned after removing one client."""
    return ClientRemoveResult(client=ClientReference(username=username))


def build_client_status_result(username: str, status: ClientMutationStatus) -> ClientStatusResult:
    """Build the stable result returned after changing one client status."""
    return ClientStatusResult(client=ClientStatus(username=username, status=status))
