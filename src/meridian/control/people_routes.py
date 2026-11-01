"""People query and bulk HTTP adapters; the catalog owns authorization."""

from __future__ import annotations

import re

from meridian.control.delivery_routes import DeliveryRequest
from meridian.control.errors import ServiceError
from meridian.control.operations_api import query_options
from meridian.core.people_query import PeoplePage, PeopleQuery
from meridian.core.queries import bounded_integer


def query_people(request: DeliveryRequest, _match: re.Match[str]) -> PeoplePage:
    defaults = {
        key: "" if value is None else "0" if value is False else str(value)
        for key, value in PeopleQuery().model_dump().items()
    }
    defaults["order"] = ""
    options = query_options(request.path, defaults)
    values: dict = dict(options)
    try:
        for field, upper in (("expires", 365), ("quota", 100), ("limit", 500)):
            values[field] = bounded_integer(options[field], field, 1, upper) if options[field] else None
        if options["never"] not in ("0", "1"):
            raise ValueError("never must be 0 or 1.")
        values["never"] = options["never"] == "1"
        values["order"] = options["order"] or None
        values["cursor"] = options["cursor"] or None
        return request.ui.service.people.query(PeopleQuery.model_validate(values))
    except ValueError as exc:
        from pydantic import ValidationError

        if isinstance(exc, ValidationError):
            raise
        raise ServiceError("invalid_request", str(exc), 400) from exc


PEOPLE_OWNER_ROUTES = frozenset({query_people})
