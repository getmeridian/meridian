"""Narrow authenticated access to the shared app matcher; rule edits stay in fleet intent."""

from __future__ import annotations

import re

from meridian.control.delivery_routes import DeliveryRequest
from meridian.control.errors import ServiceError
from meridian.control.route_metadata import NOT_FOUND
from meridian.control.route_types import ExtraCapability, route
from meridian.core.response_rule_test import ResponseRuleDecision, ResponseRuleTestRequest


def response_rules_test(request: DeliveryRequest, _match: re.Match[str]) -> ResponseRuleDecision:
    body = ResponseRuleTestRequest.model_validate_json(request.read_body())
    if body.person is not None and (
        request.caller is None
        or not request.caller.allows("people.read")
        or not request.caller.allows("people.private")
    ):
        raise ServiceError(
            "insufficient_scope", "Testing a person's response needs people.read and people.private.", 403
        )
    return request.ui.service.test_response_rules(body)


RESPONSE_RULE_ROUTES = (
    route(
        "POST",
        "/api/response-rules/test",
        ("read", "people"),
        response_rules_test,
        ResponseRuleTestRequest,
        ResponseRuleDecision,
        "Test ordered app response rules without fetching a subscription or retaining request headers.",
        credentials=False,
        capability="delivery.test",
        refusals=(NOT_FOUND,),
        extra_capabilities=(
            ExtraCapability("person", "people.read", "people"),
            ExtraCapability("private_policy", "people.private", "people"),
        ),
        description="The first custom match precedes the built-in selector. Literal User-Agent and optional Accept "
        "conditions are case-insensitive. With person, resolve private overrides above app rules and profile/fleet "
        "defaults. Header text and URLs are redacted; a block matches the anonymous unknown-token refusal.",
    ),
)
RESPONSE_RULE_OWNER_ROUTES = frozenset({response_rules_test})
