"""Transient response-rule test requests and credential-free decision contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from meridian.core.delivery_policies import ResolvedDeliveryPolicy
from meridian.core.models import CoreModel
from meridian.core.response_rules import LiteralOperator, ResponseActionKind, ResponseHeaderName
from meridian.core.subscription_formats import DocumentFormat, FormatRule, ResponseKind
from meridian.core.subscription_inputs import PreviewInputs


class ResponseRuleTestRequest(PreviewInputs):
    person: str | None = Field(default=None, min_length=1, max_length=64)


class MatchedResponseCondition(CoreModel):
    header: Literal["user-agent", "accept"]
    operator: LiteralOperator


class ResponseRuleDecision(CoreModel):
    """No input headers, token, body, or device data is returned."""

    format: DocumentFormat | None
    block: bool
    response_kind: ResponseKind
    selection: FormatRule
    action: ResponseActionKind
    reason: Literal["first_custom_match", "built_in_selector"]
    matched_conditions: list[MatchedResponseCondition] = Field(default_factory=list, max_length=2)
    fallback: FormatRule | None = Field(default=None, exclude_if=lambda value: value is None)
    policy: ResolvedDeliveryPolicy | None = Field(default=None, exclude_if=lambda value: value is None)
    headers: dict[ResponseHeaderName, str] = Field(default_factory=dict, max_length=4)
