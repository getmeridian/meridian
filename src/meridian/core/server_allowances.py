"""Reviewed server bandwidth allowances, provider labels, and payment dates."""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Annotated, Final, Literal, Self

from pydantic import BeforeValidator, Field, ValidationError, field_validator, model_validator

from meridian.core.agent_types import NodeId, Timestamp
from meridian.core.apply import CompiledApplyPreview, CompiledApplyResult
from meridian.core.client_limits import MAX_TRAFFIC_LIMIT_BYTES, ClientLimitError, parse_traffic_limit
from meridian.core.errors import MeridianError
from meridian.core.inputs import ServerReferenceValue
from meridian.core.models import CoreModel

PaymentRepeat = Literal["month", "year"]
MAX_PROVIDER_LENGTH = 80
APPROXIMATE_TRAFFIC_COVERAGE: Final = (
    "Approximate accepted user traffic (upload + download); excludes protocol overhead, "
    "the server's own downloads, and relay forwarding."
)
UNMEASURED_TRAFFIC_COVERAGE: Final = "Not measured: this server runs no traffic agent."


def _payment_date(value: object) -> object:
    if isinstance(value, str):
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
            raise ValueError("Use a payment date in YYYY-MM-DD format.")
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("Enter a valid calendar date for the payment.") from exc
    return value


PaymentDate = Annotated[date, BeforeValidator(_payment_date)]


def _provider(value: str) -> str:
    if any(
        (unicodedata.category(char).startswith("C") and char not in "\u200c\u200d") or char in "\u2028\u2029"
        for char in value
    ):
        raise ValueError("Provider must be plain text on one line.")
    return value.strip()


class ServerMetadata(CoreModel):
    """Deployer-owned metadata keyed to a server declared in the topology."""

    allowance_bytes: int | None = Field(default=None, ge=1, le=MAX_TRAFFIC_LIMIT_BYTES, strict=True)
    reset_day: int = Field(default=1, ge=1, le=31, strict=True)
    provider: str = Field(default="", max_length=MAX_PROVIDER_LENGTH)
    pay_on: PaymentDate | None = None
    pay_every: PaymentRepeat | None = None

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        return _provider(value)

    @model_validator(mode="after")
    def require_payment_date(self) -> Self:
        if self.pay_every is not None and self.pay_on is None:
            raise ValueError("Set a payment date with --pay-on before choosing --pay-every.")
        return self


class ServerMetadataChange(CoreModel):
    """Only explicitly set fields change; null clears an allowance, payment date, or repeat."""

    allowance_bytes: int | None = Field(default=None, ge=1, le=MAX_TRAFFIC_LIMIT_BYTES, strict=True)
    reset_day: int | None = Field(default=None, ge=1, le=31, strict=True)
    provider: str | None = Field(default=None, max_length=MAX_PROVIDER_LENGTH)
    pay_on: PaymentDate | None = None
    pay_every: PaymentRepeat | None = None

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str | None) -> str | None:
        return _provider(value) if value is not None else None

    def apply_to(self, current: ServerMetadata) -> ServerMetadata:
        values = current.model_dump(mode="json")
        values.update(self.model_dump(mode="json", exclude_unset=True))
        if "pay_on" in self.model_fields_set and self.pay_on is None:
            values["pay_every"] = None
        try:
            return ServerMetadata.model_validate(values)
        except ValidationError as exc:
            raise metadata_error(exc) from exc


class ServerSetRequest(CoreModel):
    query: ServerReferenceValue
    change: ServerMetadataChange
    dry_run: bool = False
    yes: bool = False


class ServerSetResult(CoreModel):
    server_ref: ServerReferenceValue
    metadata: ServerMetadata
    dry_run: bool = False
    review: CompiledApplyPreview | None = None
    applied: CompiledApplyResult | None = None
    notes: list[str] = Field(
        default_factory=lambda: [
            APPROXIMATE_TRAFFIC_COVERAGE,
            "Enabling an allowance or changing its reset day starts tracking at apply; earlier period use is unknown.",
            "Monthly reset days use UTC; a short month resets on its last day. "
            "Servers without an agent are not measured.",
        ]
    )


class ServerAllowanceSpec(CoreModel):
    """Settings for a declared server; agent identity is separate from signed node documents."""

    node_id: NodeId | None = None
    metadata: ServerMetadata


class ServerUsage(CoreModel):
    """Current UTC allowance period, using accepted traffic by receipt time, or not measured."""

    server_ref: ServerReferenceValue
    node_id: NodeId | None
    metadata: ServerMetadata
    measurement: Literal["accepted_user_traffic", "not_measured"]
    coverage: Literal[
        "Approximate accepted user traffic (upload + download); excludes protocol overhead, "
        "the server's own downloads, and relay forwarding.",
        "Not measured: this server runs no traffic agent.",
    ]
    used_bytes: int | None = Field(ge=0)
    lifetime_bytes: int | None = Field(ge=0)
    period_start: Timestamp | None
    period_end: Timestamp | None
    counting_since: Timestamp | None = Field(description="Tracking starts at apply; earlier period use is unknown.")
    next_payment_on: PaymentDate | None


class ServerUsageList(CoreModel):
    observed_at: Timestamp
    servers: list[ServerUsage]


class ServerListEntry(CoreModel):
    server_ref: ServerReferenceValue
    name: str
    host: str
    ssh_user: str
    ssh_port: int
    declared: bool
    metadata: ServerMetadata | None
    next_payment_on: PaymentDate | None = None


class ServerListResult(CoreModel):
    """Saved server profiles with their topology-owned metadata; no credentials or guessed usage."""

    servers: list[ServerListEntry]


class ServerMetadataError(MeridianError):
    def __init__(self, message: str, *, field: str = "metadata") -> None:
        self.field = field
        super().__init__(message, category="user")


def metadata_error(exc: ValidationError) -> ServerMetadataError:
    first = exc.errors()[0]
    field = str(first["loc"][0]) if first["loc"] else "pay_every"
    field = "allowance" if field == "allowance_bytes" else field
    return ServerMetadataError(str(first["msg"]).removeprefix("Value error, "), field=field)


def build_server_metadata_change(
    *,
    allowance: str | None = None,
    reset_day: str | None = None,
    provider: str | None = None,
    pay_on: str | None = None,
    pay_every: str | None = None,
) -> ServerMetadataChange:
    """Parse the CLI's options once; omission preserves the reviewed value."""
    values: dict[str, object] = {}
    if allowance is not None:
        try:
            values["allowance_bytes"] = parse_traffic_limit(allowance) or None
        except ClientLimitError as exc:
            raise ServerMetadataError(str(exc).replace("Traffic limit", "Allowance"), field="allowance") from exc
    if reset_day is not None:
        if not reset_day.strip().isdecimal() or not 1 <= int(reset_day.strip()) <= 31:
            raise ServerMetadataError("Reset day must be an integer between 1 and 31.", field="reset_day")
        values["reset_day"] = int(reset_day.strip())
    if provider is not None:
        values["provider"] = "" if provider.strip().lower() == "none" else provider
    if pay_on is not None:
        values["pay_on"] = None if pay_on.strip().lower() == "none" else pay_on.strip()
    if pay_every is not None:
        values["pay_every"] = None if pay_every.strip().lower() == "none" else pay_every.strip().lower()
    if values.get("pay_on", "omitted") is None and values.get("pay_every") is not None:
        raise ServerMetadataError(
            "A cleared payment date cannot repeat; omit --pay-every or use --pay-every none.", field="pay_repeat"
        )
    try:
        return ServerMetadataChange.model_validate(values)
    except ValidationError as exc:
        raise metadata_error(exc) from exc
