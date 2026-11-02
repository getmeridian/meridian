"""Capability-gated policy reads and private person assignments."""

from __future__ import annotations

import re

from meridian.control.delivery_routes import DeliveryRequest
from meridian.control.errors import ServiceError
from meridian.control.route_metadata import NOT_FOUND
from meridian.control.route_types import ErrorResponse, ExtraCapability, route
from meridian.core.delivery_explain import DeliveryExplainRequest, DeliveryExplanation
from meridian.core.person_delivery_policy import (
    DeliveryPolicyCatalog,
    PersonDeliveryPolicyReview,
    PersonDeliveryPolicyState,
    PersonDeliveryPolicyWrite,
)


def delivery_policy_catalog(request: DeliveryRequest, _match: re.Match[str]) -> DeliveryPolicyCatalog:
    return request.ui.service.delivery.catalog()


def explain_delivery_policy(request: DeliveryRequest, _match: re.Match[str]) -> DeliveryExplanation:
    body = DeliveryExplainRequest.model_validate_json(request.read_body())
    if body.person is not None and (
        request.caller is None
        or not request.caller.allows("people.read")
        or not request.caller.allows("people.private")
    ):
        raise ServiceError("insufficient_scope", "Person policy explanations need people.read and people.private.", 403)
    return request.ui.service.delivery.explain(body)


def person_delivery_policy(request: DeliveryRequest, match: re.Match[str]) -> PersonDeliveryPolicyState:
    return request.ui.service.delivery.person(match["username"])


def set_person_delivery_policy(request: DeliveryRequest, match: re.Match[str]) -> PersonDeliveryPolicyReview:
    return request.ui.service.delivery.set_person(
        match["username"], PersonDeliveryPolicyWrite.model_validate_json(request.read_body())
    )


DELIVERY_POLICY_ROUTES = (
    route(
        "POST",
        "/api/delivery-policies/explain",
        ("read", "people"),
        explain_delivery_policy,
        DeliveryExplainRequest,
        DeliveryExplanation,
        "Explain the first matching client rule without DNS or network requests.",
        credentials=False,
        capability="delivery.explain",
        refusals=(NOT_FOUND,),
        extra_capabilities=(
            ExtraCapability("person", "people.read", "people"),
            ExtraCapability("private_policy", "people.private", "people"),
        ),
    ),
    route(
        "GET",
        "/api/delivery-policies",
        ("read", "people"),
        delivery_policy_catalog,
        None,
        DeliveryPolicyCatalog,
        "Read the reviewed client policy catalog and profile defaults.",
        credentials=False,
        capability="delivery.read",
    ),
    route(
        "GET",
        "/api/users/{username}/delivery-policy",
        "people",
        person_delivery_policy,
        None,
        PersonDeliveryPolicyState,
        "Read a person's private routing override and effective policy.",
        credentials=False,
        capability=("people.read", "people.private"),
        refusals=(NOT_FOUND,),
    ),
    route(
        "PUT",
        "/api/users/{username}/delivery-policy",
        "people",
        set_person_delivery_policy,
        PersonDeliveryPolicyWrite,
        PersonDeliveryPolicyReview,
        "Preview or confirm a private client routing override without changing node access.",
        credentials=False,
        capability=("people.delivery", "people.read", "people.private"),
        refusals=(
            NOT_FOUND,
            ErrorResponse(400, ("unknown_delivery_policy",)),
            ErrorResponse(409, ("conflicting_delivery_policies", "stale_delivery_policy")),
        ),
        description="Explicit null follows profile/fleet defaults. confirm=false writes nothing; confirm=true requires "
        "the preview's review_hash and refuses changed membership, catalog, or override.",
    ),
)
DELIVERY_POLICY_OWNER_ROUTES = frozenset(
    {delivery_policy_catalog, person_delivery_policy, set_person_delivery_policy, explain_delivery_policy}
)
