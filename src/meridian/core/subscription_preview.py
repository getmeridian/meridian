"""Transient format inputs and a credential-free subscription explanation."""

from __future__ import annotations

import unicodedata
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, WithJsonSchema

from meridian.control.models import UserStatus
from meridian.core.models import CoreModel
from meridian.core.subscription_formats import SUFFIX_FORMATS, DocumentFormat
from meridian.core.topology import ProtocolKind

USER_AGENT_MAX_LENGTH = 512
ACCEPT_MAX_LENGTH = 256
UserAgentFamily = Literal["mihomo", "stash", "singbox", "clash", "xray-json"]
OmissionReason = Literal["unsupported_format", "disabled_endpoint", "status_placeholder", "availability_placeholder"]
EndpointRestriction = Literal["protocol", "fingerprint_copy"]
ResponseKind = Literal["subscription", "page", "account_info"]
StaleClient = Literal["outdated", "abandoned"]


def validate_preview_header(value: str) -> str:
    if any(unicodedata.category(char).startswith("C") for char in value) or any(
        char in value for char in "\u2028\u2029"
    ):
        raise ValueError("Use a single header line without control characters.")
    return value


def validate_suffix(value: str) -> str:
    if value and value not in SUFFIX_FORMATS:
        raise ValueError("Choose " + ", ".join(SUFFIX_FORMATS) + ", or leave the suffix empty.")
    return value


PreviewHeader = Annotated[str, AfterValidator(validate_preview_header)]
PreviewSuffix = Annotated[
    str,
    AfterValidator(validate_suffix),
    WithJsonSchema({"type": "string", "enum": ["", *SUFFIX_FORMATS]}),
]


class PreviewInputs(CoreModel):
    """Synthetic request headers are bounded and never stored or echoed."""

    user_agent: PreviewHeader = Field(default="", max_length=USER_AGENT_MAX_LENGTH)
    accept: PreviewHeader = Field(default="", max_length=ACCEPT_MAX_LENGTH)
    suffix: PreviewSuffix = ""


class SubscriptionPreviewRequest(PreviewInputs):
    username: str = Field(min_length=1, max_length=64)


class FormatRule(CoreModel):
    rule: Literal["suffix", "user_agent", "accept", "default"]
    family: UserAgentFamily | None = None
    suffix: PreviewSuffix = ""
    match: Literal["", "text/html"] = ""


class FormatSelection(CoreModel):
    format: DocumentFormat
    selection: FormatRule


class PreviewEntry(CoreModel):
    id: str = Field(min_length=1, max_length=256, description="Redacted service endpoint ID.")
    remark: str = Field(min_length=1, max_length=512, description="Redacted presentation name, never a URI.")
    protocol: ProtocolKind
    fingerprint_copy: bool


class OmittedEntry(PreviewEntry):
    reason: OmissionReason
    restriction: EndpointRestriction | None = None


class SubscriptionPreview(FormatSelection):
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
