"""Finite first-match app response rules, owned by reviewed delivery intent."""

from __future__ import annotations

import json
import unicodedata
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, Field, model_validator

from meridian.core.delivery_policies import DeliveryPolicySettings, delivery_policy_catalog
from meridian.core.inputs import TopologyIdValue
from meridian.core.models import CoreModel
from meridian.core.owner_contact import ContactLink
from meridian.core.subscription_formats import DocumentFormat, SubscriptionUpdateIntervalHours
from meridian.core.subscription_inputs import PreviewHeader

MAX_RESPONSE_RULES = 64
MAX_RESPONSE_RULE_BYTES = 65_536
ResponseHeaderName = Literal["profile-title", "profile-update-interval", "support-url", "announce"]
ResponseActionKind = Literal["format", "block", "policy", "headers"]
LiteralOperator = Literal["equals", "prefix", "contains"]


def response_text(value: str) -> str:
    if any(unicodedata.category(char).startswith("C") and char not in "\u200c\u200d" for char in value) or any(
        char in value for char in "\u2028\u2029"
    ):
        raise ValueError("Use one line of plain text without control characters.")
    return value


HeaderText = Annotated[str, AfterValidator(response_text)]


class LiteralCondition(CoreModel):
    """A nonempty literal; case folding never turns it into a pattern."""

    operator: LiteralOperator
    value: PreviewHeader = Field(min_length=1, max_length=256)

    def matches(self, value: str) -> bool:
        literal, candidate = self.value.casefold(), value.casefold()
        if self.operator == "equals":
            return candidate == literal
        if self.operator == "prefix":
            return candidate.startswith(literal)
        return literal in candidate

    def covers(self, other: LiteralCondition) -> bool:
        """True only when every value matching other must also match this condition."""
        broad, narrow = self.value.casefold(), other.value.casefold()
        if self.operator == "equals":
            return other.operator == "equals" and broad == narrow
        if self.operator == "prefix":
            return other.operator in ("equals", "prefix") and narrow.startswith(broad)
        return broad in narrow


class ResponseMatcher(CoreModel):
    """The User-Agent condition and optional Accept condition must both match."""

    user_agent: LiteralCondition
    accept: LiteralCondition | None = Field(default=None, exclude_if=lambda value: value is None)

    def covers(self, other: ResponseMatcher) -> bool:
        return self.user_agent.covers(other.user_agent) and (
            self.accept is None or other.accept is not None and self.accept.covers(other.accept)
        )


class ResponseHeaders(CoreModel):
    """Only headers clients consume; text is encoded by the renderer, never sent raw."""

    profile_title: HeaderText | None = Field(
        default=None, min_length=1, max_length=100, exclude_if=lambda value: value is None
    )
    update_interval_hours: SubscriptionUpdateIntervalHours | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    support_url: ContactLink | None = Field(default=None, exclude_if=lambda value: value is None)
    announce: HeaderText | None = Field(default=None, max_length=500, exclude_if=lambda value: value is None)


class FormatAction(CoreModel):
    kind: Literal["format"]
    format: DocumentFormat
    policy: TopologyIdValue | None = Field(default=None, exclude_if=lambda value: value is None)
    headers: ResponseHeaders = Field(default_factory=ResponseHeaders, exclude_if=lambda value: not value.to_data())


class BlockAction(CoreModel):
    kind: Literal["block"]


class PolicyAction(CoreModel):
    kind: Literal["policy"]
    policy: TopologyIdValue
    headers: ResponseHeaders = Field(default_factory=ResponseHeaders, exclude_if=lambda value: not value.to_data())


class HeadersAction(CoreModel):
    kind: Literal["headers"]
    headers: ResponseHeaders

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if not self.headers.to_data():
            raise ValueError("A headers action needs at least one header setting.")
        return self


ResponseAction = Annotated[FormatAction | BlockAction | PolicyAction | HeadersAction, Field(discriminator="kind")]


class ResponseRule(CoreModel):
    name: TopologyIdValue
    match: ResponseMatcher
    action: ResponseAction


class ResponseRuleSettings(DeliveryPolicySettings):
    response_rules: list[ResponseRule] = Field(
        default_factory=list, max_length=MAX_RESPONSE_RULES, exclude_if=lambda value: not value
    )

    @model_validator(mode="after")
    def reviewed_rules(self) -> Self:
        known = {policy.id for policy in delivery_policy_catalog(self)}
        names: set[str] = set()
        for index, rule in enumerate(self.response_rules):
            if rule.name in names:
                raise ValueError(f"Response rule names must be unique: {rule.name!r} is repeated.")
            names.add(rule.name)
            if isinstance(rule.action, FormatAction | PolicyAction) and (
                rule.action.policy is not None and rule.action.policy not in known
            ):
                raise ValueError(f"Response rule {rule.name!r} names an unknown delivery policy.")
            earlier = next((item for item in self.response_rules[:index] if item.match.covers(rule.match)), None)
            if earlier is not None:
                raise ValueError(f"Response rule {rule.name!r} is unreachable: {earlier.name!r} matches it first.")
        if (
            len(json.dumps([rule.to_data() for rule in self.response_rules], ensure_ascii=False).encode())
            > MAX_RESPONSE_RULE_BYTES
        ):
            raise ValueError("Response rules must fit within 64 KiB. Use fewer rules or shorter text.")
        return self
