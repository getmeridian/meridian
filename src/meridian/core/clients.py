"""Client API result models for meridian-core."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Literal, Protocol, Sequence

from pydantic import Field

from meridian.control.models import UserUsage
from meridian.core.client_limits import canonical_expiry, traffic_reset_from_strategy
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
    def lifetime_used_traffic_bytes(self) -> int: ...

    @property
    def last_traffic_reset_at(self) -> str: ...

    @property
    def traffic_limit_bytes(self) -> int: ...

    @property
    def traffic_limit_strategy(self) -> str: ...

    @property
    def expire_at(self) -> str: ...

    @property
    def created_at(self) -> str: ...

    @property
    def online_at(self) -> str: ...

    @property
    def hwid_device_limit(self) -> int | None: ...


class ClientRecord(CoreModel):
    """Redacted client metadata suitable for list views."""

    username: str
    id: str
    status: str
    traffic_used_bytes: int
    traffic_limit_bytes: int = Field(description="0 means unlimited.")
    traffic_reset: str = Field(
        description="When used traffic starts again from zero: never, day, week, month, or the panel-only "
        "month_rolling (monthly from the day the client was created).",
    )
    expires_at: str = Field(description="UTC timestamp; empty means never.")
    created_at: str
    last_seen: str
    device_limit: int | None = Field(default=None, ge=1, le=100, description="None uses the fleet's default cap.")
    declared: bool = Field(
        default=True,
        description="False for a person added in the control service's web console rather than the fleet's "
        "reviewed intent; the CLI reads them, and the console manages them.",
    )


class ClientHandoff(CoreModel):
    """Handoff links for one client: a page to open and a subscription to import."""

    share_available: bool
    subscription_available: bool
    share_url: str = ""
    subscription_url: str = ""


class ClientDetail(ClientRecord):
    """Client metadata for a detail view."""

    lifetime_traffic_bytes: int = Field(default=0, ge=0, description="Lifetime use, unaffected by traffic resets.")
    traffic_reset_at: str = Field(default="", description="UTC start of the current traffic period; empty if unknown.")


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
    usage: UserUsage | None = Field(
        default=None, description="The last seven UTC days, absent while daily history is off."
    )


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


# What a client is after enable/disable: enabling an expired or over-quota client leaves it so.
ClientMutationStatus = Literal["active", "disabled", "expired", "limited"]


class ClientStatus(CoreModel):
    """Client identity and status returned after a status mutation."""

    username: str
    status: ClientMutationStatus


class ClientStatusResult(CoreModel):
    """Result for enabling or disabling one client."""

    client: ClientStatus


class ClientLimitsResult(CoreModel):
    """Result for changing a client's limits or resetting their traffic: the client as the panel now has it."""

    client: ClientRecord


def build_client_record(user: PanelUserLike, *, declared: bool = True) -> ClientRecord:
    """Build a redacted client record from a panel user object."""
    return ClientRecord(
        username=user.username,
        id=user.id,
        status=user.status,
        traffic_used_bytes=user.used_traffic_bytes,
        traffic_limit_bytes=user.traffic_limit_bytes,
        traffic_reset=traffic_reset_from_strategy(user.traffic_limit_strategy),
        expires_at=canonical_expiry(user.expire_at),
        created_at=user.created_at,
        last_seen=user.online_at,
        device_limit=user.hwid_device_limit,
        declared=declared,
    )


def build_client_detail(user: PanelUserLike, *, declared: bool = True) -> ClientDetail:
    """Build one client detail result from panel state."""
    base = build_client_record(user, declared=declared)
    return ClientDetail(
        **base.model_dump(),
        lifetime_traffic_bytes=getattr(user, "lifetime_used_traffic_bytes", 0),
        traffic_reset_at=getattr(user, "last_traffic_reset_at", "") or "",
    )


def build_client_list_result(
    users: Sequence[PanelUserLike], *, declared: Collection[str] | None = None
) -> ClientListResult:
    """Build a client list result with status counts; `declared` (when given) marks the others undeclared."""
    records = [build_client_record(user, declared=declared is None or user.username in declared) for user in users]
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
    declared: bool = True,
    usage: UserUsage | None = None,
) -> ClientShowResult:
    """Build a one-client result with optional handoff links."""
    return ClientShowResult(
        client=build_client_detail(user, declared=declared),
        handoff=ClientHandoff(
            share_available=bool(share_url),
            subscription_available=bool(subscription_url),
            share_url=share_url,
            subscription_url=subscription_url,
        ),
        usage=usage,
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


def build_client_limits_result(user: PanelUserLike) -> ClientLimitsResult:
    """Build the result returned after changing one client's limits or resetting their traffic."""
    return ClientLimitsResult(client=build_client_record(user))
