"""Client API result models for meridian-core."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Literal, Protocol, Sequence

from pydantic import Field

from meridian.control.command_models import NodeCommandOutcome
from meridian.control.models import UserStatus, UserUsage
from meridian.core.account_schedule import AccountLifecycleState, AccountSchedulePreview
from meridian.core.client_devices import DeviceCap
from meridian.core.client_limits import canonical_expiry, traffic_reset_from_strategy
from meridian.core.languages import Language
from meridian.core.models import CoreModel
from meridian.core.people_query import PeopleFacets
from meridian.core.person_activity import PersonAttentionReason
from meridian.core.person_metadata import PersonTag, PrivateNote
from meridian.core.topology import ProtocolKind
from meridian.core.traffic import UserTrafficEvidence


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


class ClientRecord(UserTrafficEvidence, AccountLifecycleState):
    """Redacted client metadata suitable for list views."""

    username: str
    id: str
    status: str
    traffic_used_bytes: int
    traffic_limit_bytes: int = Field(description="0 means unlimited.")
    traffic_reset: str = Field(
        description="When used traffic resets: never, day, week, month, or month_rolling on the stored UTC anchor day.",
    )
    expires_at: str = Field(description="UTC timestamp; empty means never.")
    created_at: str
    last_seen: str
    attention_reasons: list[PersonAttentionReason] = Field(
        default_factory=list, description="Derived reasons when client list --attention was requested; never stored."
    )
    device_limit: DeviceCap | None = Field(default=None, description="Null uses the fleet's cap; 0 means unlimited.")
    note: PrivateNote = Field(default="", repr=False, description="Private operator note; people/admin only.")
    tag: PersonTag = Field(default="", repr=False, description="Private operator tag; people/admin only.")
    language: Language | None = Field(
        default=None, description="Subscription status language; null uses the fleet default."
    )
    schedule: AccountSchedulePreview | None = None
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


class ClientConnection(CoreModel):
    """One canonical subscription entry; URI and QR are credentials, never passive output."""

    remark: str
    protocol: ProtocolKind | None = Field(description="Null for an inert availability or status notice.")
    available: bool
    uri: str = Field(repr=False)
    qr_svg: str = Field(default="", repr=False, description="Local SVG data URI in an owner-console share view.")


SubscriptionFormat = Literal["xray-json", "mihomo", "singbox"]


class SubscriptionFormatUrl(CoreModel):
    """A credential-bearing URL for a format the control service already serves."""

    format: SubscriptionFormat
    url: str = Field(repr=False)


class ClientConnections(CoreModel):
    """Producing handoff view, shared by the API, console, and explicit CLI inspection."""

    username: str
    status: UserStatus
    profile_title: str
    subscription_url: str = Field(repr=False)
    connections: list[ClientConnection] = Field(default_factory=list)
    formats: list[SubscriptionFormatUrl] = Field(default_factory=list)


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
    total: int | None = Field(default=None, exclude_if=lambda value: value is None)
    next_cursor: str | None = Field(default=None, exclude_if=lambda value: value is None)
    facets: PeopleFacets | None = Field(default=None, exclude_if=lambda value: value is None)


class ClientShowResult(CoreModel):
    """Result for showing one client."""

    client: ClientDetail
    handoff: ClientHandoff
    connections: ClientConnections | None = Field(default=None, description="Only requested by --connections.")
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
    cut_off: list[NodeCommandOutcome] | None = Field(
        default=None, description="Per-node open-session results when --cut-off was requested."
    )


class ClientReference(CoreModel):
    """Public identity of a client mutation target."""

    username: str


class ClientRemoveResult(CoreModel):
    """Result for removing one client."""

    client: ClientReference
    cut_off: list[NodeCommandOutcome] | None = Field(
        default=None, description="Per-node open-session results when --cut-off was requested."
    )


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
    schedule_preview: AccountSchedulePreview | None = Field(default=None, exclude_if=lambda value: value is None)
    applied: bool = Field(default=True, exclude_if=lambda value: value is True)


def client_metadata_paths(records: Sequence[ClientRecord] | None = None) -> frozenset[str]:
    """Exact metadata paths in an authorized single-person or list JSON result."""
    bases = ["data.client"] if records is None else [f"data.clients.{index}" for index in range(len(records))]
    return frozenset(f"{base}.{field}" for base in bases for field in ("note", "tag"))


def connection_credential_paths(view: ClientConnections | None) -> frozenset[str]:
    """Exact paths revealed only in the final client-show output that produced this view."""
    if view is None:
        return frozenset()
    return frozenset(
        {"data.connections.subscription_url"}
        | {
            f"data.connections.connections.{index}.{field}"
            for index in range(len(view.connections))
            for field in ("uri", "qr_svg")
        }
        | {f"data.connections.formats.{index}.url" for index in range(len(view.formats))}
    )


def build_client_record(
    user: PanelUserLike, *, declared: bool = True, attention_reasons: Sequence[PersonAttentionReason] = ()
) -> ClientRecord:
    """Build a redacted client record from a panel user object."""
    return ClientRecord(
        **AccountLifecycleState.model_validate(
            {
                field: getattr(user, field, model_field.default)
                for field, model_field in AccountLifecycleState.model_fields.items()
            }
        ).model_dump(),
        username=user.username,
        id=user.id,
        status=user.status,
        traffic_used_bytes=user.used_traffic_bytes,
        traffic_limit_bytes=user.traffic_limit_bytes,
        traffic_reset=traffic_reset_from_strategy(user.traffic_limit_strategy),
        expires_at=canonical_expiry(user.expire_at),
        created_at=user.created_at,
        last_seen=user.online_at,
        attention_reasons=list(attention_reasons),
        first_traffic_at=getattr(user, "first_traffic_at", None),
        last_traffic_node=getattr(user, "last_traffic_node", None),
        device_limit=user.hwid_device_limit,
        note=getattr(user, "description", ""),
        tag=getattr(user, "tag", ""),
        language=getattr(user, "language", None),
        schedule=getattr(user, "schedule", None),
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
    users: Sequence[PanelUserLike],
    *,
    declared: Collection[str] | None = None,
    attention: Mapping[str, Sequence[PersonAttentionReason]] | None = None,
) -> ClientListResult:
    """Build a client list result with status counts; `declared` (when given) marks the others undeclared."""
    records = [
        build_client_record(
            user,
            declared=declared is None or user.username in declared,
            attention_reasons=attention.get(user.username, ()) if attention is not None else (),
        )
        for user in users
        if attention is None or user.username in attention
    ]
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
    connections: ClientConnections | None = None,
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
        connections=connections,
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


def build_client_reissue_result(
    user: PanelUserLike, *, page_url: str, subscription_url: str, cut_off: Sequence[NodeCommandOutcome] | None = None
) -> ClientReissueResult:
    """Build the result returned after reissuing one client's links."""
    return ClientReissueResult(
        client=ClientCreated(
            username=user.username,
            id=user.id,
            status=user.status.lower(),
            page_url=page_url,
            subscription_url=subscription_url,
        ),
        cut_off=list(cut_off) if cut_off is not None else None,
    )


def build_client_remove_result(
    username: str, *, cut_off: Sequence[NodeCommandOutcome] | None = None
) -> ClientRemoveResult:
    """Build the stable result returned after removing one client."""
    return ClientRemoveResult(
        client=ClientReference(username=username), cut_off=list(cut_off) if cut_off is not None else None
    )


def build_client_status_result(username: str, status: ClientMutationStatus) -> ClientStatusResult:
    """Build the stable result returned after changing one client status."""
    return ClientStatusResult(client=ClientStatus(username=username, status=status))


def build_client_limits_result(user: PanelUserLike, *, declared: bool = True) -> ClientLimitsResult:
    """Build the result returned after changing one client's limits or resetting their traffic."""
    return ClientLimitsResult(client=build_client_record(user, declared=declared))
