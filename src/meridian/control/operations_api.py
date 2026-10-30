"""Read adapters for local events and derived attention, with bounded query options."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from meridian.control.errors import ServiceError
from meridian.control.service import ControlService
from meridian.core.agent_types import MAX_SAFE_INTEGER
from meridian.core.fleet_events import MAX_EVENTS, FleetEventPage
from meridian.core.queries import bounded_integer


def query_options(path: str, defaults: dict[str, str]) -> dict[str, str]:
    query = parse_qs(urlsplit(path).query, keep_blank_values=True)
    unknown = sorted(query.keys() - defaults.keys())
    if unknown:
        raise ServiceError("invalid_request", f"Unknown query option {unknown[0]!r}.", 400)
    values = defaults.copy()
    for name, options in query.items():
        if len(options) != 1:
            raise ServiceError("invalid_request", f"{name} must appear once.", 400)
        values[name] = options[0]
    return values


def read_events(service: ControlService, path: str) -> FleetEventPage:
    options = query_options(path, {"after": "0", "limit": "100"})
    try:
        after = bounded_integer(options["after"], "after", 0, MAX_SAFE_INTEGER)
        limit = bounded_integer(options["limit"], "limit", 1, MAX_EVENTS)
    except ValueError as exc:
        raise ServiceError("invalid_request", str(exc), 400) from exc
    return service.events.list(after=after, limit=limit)
