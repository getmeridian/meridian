"""Local lifecycle events consumed by fleet readers and future notification adapters.

These are durable operational facts, kept apart from command progress streams.
Details have a closed shape per kind: field names, statuses, counts, audit IDs,
and token names/scopes. No credentials, client addresses, device data, or raw report errors.
"""

from __future__ import annotations

from typing import Literal, TypeAlias, get_args

from pydantic import Field, model_validator

from meridian.control.models import TokenScope, UserStatus
from meridian.core.agent_protocol import CommandEffect, CommandKind, CommandResultStatus
from meridian.core.agent_types import MAX_SAFE_INTEGER, CommandId, Timestamp
from meridian.core.models import CoreModel
from meridian.core.people_bulk import BulkActionName

MAX_EVENTS = 2000
EVENT_RETENTION_DAYS = 180
FleetEventKind = Literal[
    "people.bulk",
    "user.created",
    "user.updated",
    "user.enabled",
    "user.disabled",
    "user.reissued",
    "user.removed",
    "user.traffic_reset",
    "user.expired",
    "user.limited",
    "user.restored",
    "node.offline",
    "node.online",
    "node.apply_failed",
    "node.apply_recovered",
    "command.issued",
    "command.result",
    "privacy.changed",
    "notice.changed",
    "language.changed",
    "token.created",
    "token.revoked",
]
FLEET_EVENT_KINDS: tuple[FleetEventKind, ...] = get_args(FleetEventKind)
UserEventField = Literal[
    "status",
    "device_limit",
    "expire_at",
    "traffic_limit_bytes",
    "traffic_limit_strategy",
    "credentials",
    "created_at",
    "traffic_reset_at",
    "lifetime_traffic_bytes",
    "language",
    "traffic_reset_anchor_day",
    "activation_duration_days",
    "renewal_interval_days",
    "renewal_interval_months",
    "renewal_limit",
    "renewals_completed",
    "renewal_anchor_at",
]
_NAME_PATTERN = r"^[^\x00-\x1f\x7f]+$"


class EmptyEventDetail(CoreModel):
    """A fact needs no additional stored data."""


class UserFieldsDetail(CoreModel):
    fields: list[UserEventField] = Field(min_length=1, max_length=17, description="Changed field names, never values.")

    @model_validator(mode="after")
    def unique_fields(self) -> UserFieldsDetail:
        if len(self.fields) != len(set(self.fields)):
            raise ValueError("Changed fields must be unique.")
        return self


class UserStateDetail(CoreModel):
    previous_status: UserStatus


class NodeEventDetail(CoreModel):
    reason: Literal["live_window_elapsed", "agent_seen", "apply_failed", "apply_recovered"]
    failed_resources: int = Field(
        default=0, ge=0, description="Only a count; raw errors and resource values never ride."
    )


class PrivacyEventDetail(CoreModel):
    audit_id: int = Field(
        ge=1, le=MAX_SAFE_INTEGER, description="The row in privacy_changes, also exposed in the audit."
    )


class CommandIssuedDetail(CoreModel):
    command_id: CommandId
    kind: CommandKind


class CommandResultDetail(CommandIssuedDetail):
    status: CommandResultStatus
    effect: CommandEffect | None = None


class TokenEventDetail(CoreModel):
    name: str = Field(min_length=1, max_length=64, pattern=_NAME_PATTERN)
    scope: TokenScope


class BulkEventDetail(CoreModel):
    action: BulkActionName | Literal["undo"]
    targeted: int = Field(ge=0)
    changed: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    cli_required: int = Field(ge=0)


EventDetail: TypeAlias = (
    BulkEventDetail
    | EmptyEventDetail
    | UserFieldsDetail
    | UserStateDetail
    | NodeEventDetail
    | PrivacyEventDetail
    | TokenEventDetail
    | CommandIssuedDetail
    | CommandResultDetail
)
_DETAIL_TYPES: dict[FleetEventKind, type[CoreModel]] = {
    "people.bulk": BulkEventDetail,
    "user.created": EmptyEventDetail,
    "user.enabled": EmptyEventDetail,
    "user.disabled": EmptyEventDetail,
    "user.reissued": EmptyEventDetail,
    "user.removed": EmptyEventDetail,
    "user.traffic_reset": EmptyEventDetail,
    "user.updated": UserFieldsDetail,
    "user.expired": UserStateDetail,
    "user.limited": UserStateDetail,
    "user.restored": UserStateDetail,
    "node.offline": NodeEventDetail,
    "node.online": NodeEventDetail,
    "node.apply_failed": NodeEventDetail,
    "node.apply_recovered": NodeEventDetail,
    "command.issued": CommandIssuedDetail,
    "command.result": CommandResultDetail,
    "privacy.changed": PrivacyEventDetail,
    "notice.changed": EmptyEventDetail,
    "language.changed": EmptyEventDetail,
    "token.created": TokenEventDetail,
    "token.revoked": TokenEventDetail,
}


class FleetEvent(CoreModel):
    """One secret-free lifecycle fact in this fleet's bounded local ledger."""

    id: int = Field(ge=1, le=MAX_SAFE_INTEGER, description="Monotonic cursor, never reused after retention pruning.")
    time: Timestamp
    kind: FleetEventKind
    actor: str = Field(
        min_length=1, max_length=64, pattern=_NAME_PATTERN, description="Token name, owner, root, or system."
    )
    subject: str = Field(
        min_length=1, max_length=64, pattern=_NAME_PATTERN, description="Username, node ID, privacy, or token name."
    )
    detail: EventDetail = Field(default_factory=EmptyEventDetail)

    @model_validator(mode="after")
    def kind_detail(self) -> FleetEvent:
        if type(self.detail) is not _DETAIL_TYPES[self.kind]:
            raise ValueError("The event detail does not match its kind.")
        return self


class FleetEventQuery(CoreModel):
    after: int = Field(default=0, ge=0, le=MAX_SAFE_INTEGER)
    limit: int = Field(default=100, ge=1, le=MAX_EVENTS)


class FleetEventPage(CoreModel):
    """Events oldest first after the cursor; use next_cursor as the next after or --since."""

    events: list[FleetEvent] = Field(max_length=MAX_EVENTS)
    next_cursor: int = Field(ge=0, le=MAX_SAFE_INTEGER)
    has_more: bool
