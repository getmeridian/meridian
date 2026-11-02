"""Profile reviews and effective-access API adapters; policy edits remain deployer-owned."""

from __future__ import annotations

import re

from meridian.control.delivery_routes import DeliveryRequest
from meridian.control.route_metadata import NOT_FOUND
from meridian.control.route_types import ErrorResponse, route
from meridian.core.access_profile_views import (
    AccessProfileCatalog,
    AccessProfileReview,
    AccessProfileReviewRequest,
    PersonAccess,
)


def access_profile_catalog(request: DeliveryRequest, _match: re.Match[str]) -> AccessProfileCatalog:
    return request.ui.service.access.catalog()


def access_profile_preview(request: DeliveryRequest, _match: re.Match[str]) -> AccessProfileReview:
    return request.ui.service.access.preview(AccessProfileReviewRequest.model_validate_json(request.read_body()))


def person_access(request: DeliveryRequest, match: re.Match[str]) -> PersonAccess:
    return request.ui.service.access.person(match["username"])


ACCESS_PROFILE_ROUTES = (
    route(
        "GET",
        "/api/access-profiles",
        "people",
        access_profile_catalog,
        None,
        AccessProfileCatalog,
        "Read reviewed profiles and private membership counts.",
        credentials=False,
        capability=("people.read", "people.private"),
    ),
    route(
        "POST",
        "/api/access-profiles/preview",
        "admin",
        access_profile_preview,
        AccessProfileReviewRequest,
        AccessProfileReview,
        "Review who gains or loses access before applying a topology change.",
        credentials=False,
        capability=("fleet.apply", "people.private"),
        refusals=(ErrorResponse(409, ("profile_in_use",)),),
        description="Read-only. A grant covers every endpoint sharing its authentication inbound. "
        "The returned access_hash fences membership at apply.",
    ),
    route(
        "GET",
        "/api/users/{username}/access",
        "people",
        person_access,
        None,
        PersonAccess,
        "Read a person's effective grants and the profiles that allow each connection.",
        credentials=False,
        capability=("people.read", "people.private"),
        refusals=(NOT_FOUND,),
    ),
)
ACCESS_PROFILE_OWNER_ROUTES = frozenset({access_profile_catalog, person_access})
