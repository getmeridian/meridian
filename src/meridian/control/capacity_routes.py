"""Bounded server capacity reads with the same fleet read grant as node health."""

import re
from typing import Protocol

from meridian.control.errors import ServiceError
from meridian.control.operations_api import query_options
from meridian.control.service import ControlService
from meridian.control.ui_routes import UiRequest
from meridian.core.capacity import CAPACITY_RETENTION_HOURS, CapacityQuery, NodeCapacityView
from meridian.core.queries import bounded_integer


class CapacityRequest(UiRequest, Protocol):
    path: str


def node_capacity(request: CapacityRequest, match: re.Match[str]) -> NodeCapacityView:
    service: ControlService = request.ui.service
    options = query_options(request.path, {"hours": str(CapacityQuery().hours)})
    try:
        query = CapacityQuery(hours=bounded_integer(options["hours"], "hours", 1, CAPACITY_RETENTION_HOURS))
    except ValueError as error:
        raise ServiceError("invalid_request", str(error), 400) from error
    return service.capacity.view(match["node_id"], hours=query.hours)
