"""Transient format inputs and a credential-free subscription explanation."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from meridian.control.models import UserStatus
from meridian.core.delivery_explain import DeliveryExplanation, parse_destination
from meridian.core.delivery_preview import DeliveryPolicyPreview
from meridian.core.models import CoreModel
from meridian.core.response_rule_test import ResponseRuleDecision
from meridian.core.subscription_formats import DocumentFormat, FormatRule, ResponseKind
from meridian.core.subscription_inputs import PreviewInputs
from meridian.core.topology import ProtocolKind

OmissionReason = Literal[
    "unsupported_format", "disabled_endpoint", "status_placeholder", "availability_placeholder", "access_profile"
]
EndpointRestriction = Literal["protocol", "fingerprint_copy"]
StaleClient = Literal["outdated", "abandoned"]


class SubscriptionPreviewRequest(PreviewInputs):
    username: str = Field(min_length=1, max_length=64)
    destination: str | None = Field(default=None, min_length=1, max_length=512, exclude_if=lambda value: value is None)

    @field_validator("destination")
    @classmethod
    def destination_value(cls, value: str | None) -> str | None:
        if value is not None:
            parse_destination(value)
        return value


class PreviewEntry(CoreModel):
    id: str = Field(min_length=1, max_length=256, description="Redacted service endpoint ID.")
    remark: str = Field(min_length=1, max_length=512, description="Redacted presentation name, never a URI.")
    protocol: ProtocolKind
    fingerprint_copy: bool


class OmittedEntry(PreviewEntry):
    reason: OmissionReason
    restriction: EndpointRestriction | None = None


class SubscriptionPreview(CoreModel):
    format: DocumentFormat | None
    selection: FormatRule
    response_rule: ResponseRuleDecision | None = Field(default=None, exclude_if=lambda value: value is None)
    delivery: DeliveryPolicyPreview | None = Field(default=None, exclude_if=lambda value: value is None)
    explanation: DeliveryExplanation | None = Field(default=None, exclude_if=lambda value: value is None)
    username: str = Field(min_length=1, max_length=256, description="Redacted account name.")
    status: UserStatus
    response_kind: ResponseKind
    protocols: list[ProtocolKind] = Field(max_length=4)
    included: list[PreviewEntry] = Field(max_length=10_000)
    omitted: list[OmittedEntry] = Field(max_length=10_000)
    placeholder: Literal["disabled", "limited", "expired", "no_connections"] | None
    stale_client: StaleClient | None = Field(
        default=None,
        description="Why one inert update or switch entry follows the connections; the User-Agent is never stored.",
    )
    response_headers: dict[str, str] = Field(
        description="Service response headers; URIs and encoded text are redacted."
    )
