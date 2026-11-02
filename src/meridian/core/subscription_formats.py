"""Shared document vocabulary and explicit suffixes for subscriptions and previews."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, Field, WithJsonSchema

from meridian.core.inputs import TopologyIdValue
from meridian.core.models import CoreModel

DocumentFormat = Literal["browser", "mihomo", "stash", "singbox", "xray-json", "base64"]
ResponseKind = Literal["subscription", "page", "account_info", "blocked"]
SUFFIX_FORMATS: dict[str, DocumentFormat] = {
    "info": "browser",
    "json": "xray-json",
    "v2ray-json": "xray-json",
    "mihomo": "mihomo",
    "clash": "mihomo",
    "stash": "stash",
    "singbox": "singbox",
}
SubscriptionUpdateIntervalHours = Annotated[int, Field(ge=1, le=168)]
UserAgentFamily = Literal["mihomo", "stash", "singbox", "clash", "xray-json"]


def validate_suffix(value: str) -> str:
    if value and value not in SUFFIX_FORMATS:
        raise ValueError("Choose " + ", ".join(SUFFIX_FORMATS) + ", or leave the suffix empty.")
    return value


PreviewSuffix = Annotated[
    str,
    AfterValidator(validate_suffix),
    WithJsonSchema({"type": "string", "enum": ["", *SUFFIX_FORMATS]}),
]


class FormatRule(CoreModel):
    rule: Literal["suffix", "user_agent", "accept", "default", "custom"]
    family: UserAgentFamily | None = None
    suffix: PreviewSuffix = ""
    match: Literal["", "text/html"] = ""
    name: TopologyIdValue | None = Field(default=None, exclude_if=lambda value: value is None)
    number: int | None = Field(default=None, ge=1, le=64, exclude_if=lambda value: value is None)


class FormatSelection(CoreModel):
    format: DocumentFormat
    selection: FormatRule


def subscription_response_kind(document: DocumentFormat | None, suffix: str) -> ResponseKind:
    if document is None:
        return "blocked"
    if document == "browser":
        return "account_info" if suffix else "page"
    return "subscription"
