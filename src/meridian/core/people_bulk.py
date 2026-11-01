"""Reviewed bulk person changes, content-free counts, and durable receipts."""

from __future__ import annotations

from typing import Annotated, Any, Literal, cast

from pydantic import Field, GetJsonSchemaHandler, SerializerFunctionWrapHandler, model_serializer, model_validator
from pydantic_core import CoreSchema

from meridian.control.models import USERNAME_PATTERN, UserRecord, UserStatus
from meridian.core.account_schedule import AccountLifecycleState
from meridian.core.agent_types import Sha256Hex, Timestamp
from meridian.core.client_devices import DeviceCap
from meridian.core.client_limits import MAX_TRAFFIC_LIMIT_BYTES, LimitStrategy
from meridian.core.models import CoreModel
from meridian.core.people_query import PeopleQuery
from meridian.core.person_metadata import PersonTag
from meridian.core.traffic import PeriodTraffic

BulkActionName = Literal[
    "extend", "expiry", "quota", "reset-traffic", "enable", "disable", "tag", "devices", "renewal", "delete"
]
BulkOutcome = Literal["changed", "unchanged", "cli_required"]
BulkWarning = Literal[
    "irreversible",
    "period_reset",
    "topology_delete",
    "disabled",
    "expiry_blocks_enable",
    "quota_blocks_enable",
    "no_change",
]
OperationId = str


class BulkAction(CoreModel):
    action: BulkActionName
    days: int | None = Field(default=None, ge=1, le=3650, strict=True)
    months: int | None = Field(default=None, ge=1, le=120, strict=True)
    expire_at: Timestamp | None = None
    traffic_limit_bytes: int | None = Field(default=None, ge=0, le=MAX_TRAFFIC_LIMIT_BYTES, strict=True)
    traffic_limit_strategy: LimitStrategy | None = None
    traffic_reset_anchor_day: int | None = Field(default=None, ge=1, le=31, strict=True)
    tag: PersonTag = ""
    device_limit: DeviceCap | None = None
    renewal_interval_days: int | None = Field(default=None, ge=1, le=3650, strict=True)
    renewal_interval_months: int | None = Field(default=None, ge=1, le=120, strict=True)
    renewal_limit: int | None = Field(default=None, ge=1, le=10000, strict=True)

    def model_post_init(self, _context: Any) -> None:
        self.action_parameters()

    def action_parameters(self) -> BulkAction:
        allowed = {
            "extend": {"days", "months"},
            "expiry": {"expire_at"},
            "quota": {"traffic_limit_bytes", "traffic_limit_strategy", "traffic_reset_anchor_day"},
            "tag": {"tag"},
            "devices": {"device_limit"},
            "renewal": {"renewal_interval_days", "renewal_interval_months", "renewal_limit"},
        }.get(self.action, set())
        if self.model_fields_set - {"action"} - allowed:
            raise ValueError(f"Only {', '.join(sorted(allowed)) or 'no parameters'} apply to {self.action}.")
        if self.action == "extend" and ((self.days is None) == (self.months is None)):
            raise ValueError("Choose days or calendar months for the extension.")
        required = {"expiry": "expire_at", "tag": "tag", "devices": "device_limit"}
        if self.action in required and required[self.action] not in self.model_fields_set:
            raise ValueError(f"Supply {required[self.action]} explicitly, including when clearing it.")
        if self.action == "quota" and not self.model_fields_set & {
            "traffic_limit_bytes",
            "traffic_limit_strategy",
            "traffic_reset_anchor_day",
        }:
            raise ValueError("Choose a quota or reset strategy to change.")
        if (
            self.action == "quota"
            and "traffic_limit_bytes" in self.model_fields_set
            and self.traffic_limit_bytes is None
        ):
            raise ValueError("Set a quota in bytes; 0 clears it.")
        if self.action == "renewal":
            if not self.model_fields_set & {"renewal_interval_days", "renewal_interval_months", "renewal_limit"}:
                raise ValueError("Set a renewal interval or cap, or explicitly clear renewal.")
            if self.renewal_interval_days is not None and self.renewal_interval_months is not None:
                raise ValueError("Choose a renewal interval in days or calendar months.")
        return self

    @model_serializer(mode="wrap")
    def supplied_parameters(self, handler: SerializerFunctionWrapHandler) -> dict:
        return {key: value for key, value in handler(self).items() if key in self.model_fields_set}

    @classmethod
    def __get_pydantic_json_schema__(cls, schema: CoreSchema, handler: GetJsonSchemaHandler) -> dict:
        model_schema = dict(schema)
        model_schema.pop("serialization", None)
        return handler(cast(CoreSchema, model_schema))

    def parameters(self) -> dict:
        return self.model_dump(mode="json", include=self.model_fields_set)


class BulkPreviewRequest(CoreModel):
    action: BulkAction
    usernames: list[Annotated[str, Field(pattern=rf"^{USERNAME_PATTERN}$")]] | None = Field(
        default=None, min_length=1, max_length=50000
    )
    query: PeopleQuery | None = None

    @model_validator(mode="after")
    def one_selection(self) -> BulkPreviewRequest:
        if (self.usernames is None) == (self.query is None):
            raise ValueError("Choose explicit usernames or everyone matching a query.")
        if self.usernames is not None and len(set(self.usernames)) != len(self.usernames):
            raise ValueError("A person must appear only once in the selection.")
        if self.query is not None and self.query.cursor:
            raise ValueError("Bulk selection needs a query without a cursor.")
        return self


class BulkFields(AccountLifecycleState):
    status: UserStatus
    expire_at: Timestamp | None
    traffic_limit_bytes: int
    traffic_limit_strategy: LimitStrategy
    device_limit: DeviceCap | None
    tag: PersonTag
    traffic_reset_at: Timestamp | None
    used_traffic_bytes: int
    period_traffic: PeriodTraffic

    @classmethod
    def from_user(cls, user: UserRecord) -> BulkFields:
        return cls.model_validate({name: getattr(user, name) for name in cls.model_fields})


BULK_WRITABLE_FIELDS = tuple(
    name for name in BulkFields.model_fields if name not in {"traffic_reset_at", "used_traffic_bytes", "period_traffic"}
)


class BulkPersonChange(CoreModel):
    username: str
    display_name: str
    row_version: int = Field(ge=1)
    before: BulkFields
    after: BulkFields | None
    changes: list[str]
    outcome: BulkOutcome
    cli_command: str | None = None
    warnings: list[BulkWarning] = Field(default_factory=list)


class BulkCounts(CoreModel):
    targeted: int = Field(ge=0)
    changed: int = Field(ge=0)
    unchanged: int = Field(ge=0)
    cli_required: int = Field(ge=0)


class BulkPreview(CoreModel):
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    preview_hash: Sha256Hex
    created_at: Timestamp
    expires_at: Timestamp
    action: BulkAction
    people: list[BulkPersonChange]
    counts: BulkCounts
    changed_nodes: int = Field(ge=0)
    reversible: bool
    warnings: list[BulkWarning] = Field(default_factory=list)


class BulkCommitRequest(CoreModel):
    preview_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    preview_hash: Sha256Hex


class BulkUndoRequest(CoreModel):
    receipt_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class BulkReceipt(CoreModel):
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    preview_hash: Sha256Hex
    action: BulkAction
    committed_at: Timestamp
    counts: BulkCounts
    people: list[BulkPersonChange]
    changed_nodes: int = Field(ge=0)
    undo_until: Timestamp | None
    undone_at: Timestamp | None = None
    undo_people: list[BulkPersonChange] | None = None


class ClientBulkResult(CoreModel):
    preview: BulkPreview | None = None
    receipt: BulkReceipt | None = None
    applied: bool
