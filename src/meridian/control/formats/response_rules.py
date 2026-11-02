"""One pure app decision for live delivery, the CLI, and authenticated previews."""

from __future__ import annotations

import base64
from collections.abc import Sequence

from meridian.control.formats.selector import explain_format
from meridian.core.delivery_policies import resolve_delivery_policy
from meridian.core.response_rule_test import MatchedResponseCondition, ResponseRuleDecision
from meridian.core.response_rules import (
    BlockAction,
    FormatAction,
    PolicyAction,
    ResponseHeaderName,
    ResponseHeaders,
    ResponseRuleSettings,
)
from meridian.core.subscription_formats import FormatRule, subscription_response_kind
from meridian.core.subscription_inputs import PreviewInputs


def evaluate_response_rules(
    settings: ResponseRuleSettings,
    *,
    user_agent: str | None = None,
    accept: str | None = None,
    suffix: str = "",
    profiles: Sequence[str] = (),
    person_override: str | None = None,
) -> ResponseRuleDecision:
    """First custom match wins; format overrides precede suffix, Accept, and built-in app rules.

    Header/policy actions keep the built-in format selection. The shared policy resolver owns
    person > response rule > agreeing profiles > fleet precedence. Request values never enter
    the result; a block resolves neither policy nor headers.
    """
    inputs = PreviewInputs(user_agent=user_agent or "", accept=accept or "", suffix=suffix)
    for number, rule in enumerate(settings.response_rules, 1):
        if not rule.match.user_agent.matches(inputs.user_agent):
            continue
        if rule.match.accept is not None and not rule.match.accept.matches(inputs.accept):
            continue
        action = rule.action
        selection = FormatRule(rule="custom", name=rule.name, number=number, suffix=suffix)
        matched_conditions = [MatchedResponseCondition(header="user-agent", operator=rule.match.user_agent.operator)]
        if rule.match.accept is not None:
            matched_conditions.append(MatchedResponseCondition(header="accept", operator=rule.match.accept.operator))
        if isinstance(action, BlockAction):
            return ResponseRuleDecision(
                format=None,
                block=True,
                response_kind="blocked",
                selection=selection,
                action="block",
                reason="first_custom_match",
                matched_conditions=matched_conditions,
            )
        policy = resolve_delivery_policy(
            settings,
            profiles=profiles,
            person_override=person_override,
            response_override=action.policy if isinstance(action, FormatAction | PolicyAction) else None,
        )
        if isinstance(action, FormatAction):
            document, fallback = action.format, None
        else:
            built_in = explain_format(inputs.user_agent, inputs.accept, inputs.suffix)
            document, fallback = built_in.format, built_in.selection
        return ResponseRuleDecision(
            format=document,
            block=False,
            response_kind=subscription_response_kind(document, suffix),
            selection=selection,
            action=action.kind,
            reason="first_custom_match",
            matched_conditions=matched_conditions,
            fallback=fallback,
            policy=policy,
            headers=render_response_headers(action.headers),
        )
    built_in = explain_format(inputs.user_agent, inputs.accept, inputs.suffix)
    return ResponseRuleDecision(
        **built_in.to_data(),
        block=False,
        response_kind=subscription_response_kind(built_in.format, suffix),
        action="format",
        reason="built_in_selector",
        policy=resolve_delivery_policy(settings, profiles=profiles, person_override=person_override),
    )


def encoded_header_text(value: str) -> str:
    return "base64:" + base64.b64encode(value.encode("utf-8")).decode("ascii")


def render_response_headers(headers: ResponseHeaders) -> dict[ResponseHeaderName, str]:
    """Empty support/announce values remove the inherited header; null leaves it alone."""
    result: dict[ResponseHeaderName, str] = {}
    if headers.profile_title is not None:
        result["profile-title"] = encoded_header_text(headers.profile_title)
    if headers.update_interval_hours is not None:
        result["profile-update-interval"] = str(headers.update_interval_hours)
    if headers.support_url is not None:
        result["support-url"] = headers.support_url
    if headers.announce is not None:
        result["announce"] = encoded_header_text(headers.announce) if headers.announce else ""
    return result


def merge_response_headers(headers: dict[str, str], changes: dict[ResponseHeaderName, str]) -> dict[str, str]:
    result = dict(headers)
    for name, value in changes.items():
        if value:
            result[name] = value
        else:
            result.pop(name, None)
    return result
