"""Read adapters for local events and derived attention, with bounded query options."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from meridian.control.errors import ServiceError
from meridian.control.service import ControlService
from meridian.core.agent_types import MAX_SAFE_INTEGER
from meridian.core.attention import (
    MAX_EXPIRY_DAYS,
    MAX_PAYMENT_DAYS,
    MAX_UNUSED_DAYS,
    AttentionList,
    AttentionThresholds,
)
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


def read_history_days(path: str) -> int:
    """The history endpoints' single day count, preserving their existing defaults and bounds."""
    values = parse_qs(urlsplit(path).query, keep_blank_values=True).get("days", ["30"])
    if len(values) != 1 or not re.fullmatch(r"[0-9]{1,3}", values[0]) or not 1 <= int(values[0]) <= 730:
        raise ServiceError("invalid_request", "days must be one integer between 1 and 730.", 400)
    return int(values[0])


def read_events(service: ControlService, path: str) -> FleetEventPage:
    options = query_options(path, {"after": "0", "limit": "100"})
    try:
        after = bounded_integer(options["after"], "after", 0, MAX_SAFE_INTEGER)
        limit = bounded_integer(options["limit"], "limit", 1, MAX_EVENTS)
    except ValueError as exc:
        raise ServiceError("invalid_request", str(exc), 400) from exc
    return service.events.list(after=after, limit=limit)


def read_attention(service: ControlService, path: str) -> AttentionList:
    defaults = AttentionThresholds()
    options = query_options(path, {name: str(value) for name, value in defaults.model_dump().items()})
    try:
        thresholds = AttentionThresholds(
            expiry_days=bounded_integer(options["expiry_days"], "expiry_days", 1, MAX_EXPIRY_DAYS),
            quota_percent=bounded_integer(options["quota_percent"], "quota_percent", 1, 100),
            unused_days=bounded_integer(options["unused_days"], "unused_days", 1, MAX_UNUSED_DAYS),
            allowance_percent=bounded_integer(options["allowance_percent"], "allowance_percent", 1, 100),
            allowance_exceeded_percent=bounded_integer(
                options["allowance_exceeded_percent"], "allowance_exceeded_percent", 1, 100
            ),
            payment_days=bounded_integer(options["payment_days"], "payment_days", 1, MAX_PAYMENT_DAYS),
        )
    except ValueError as exc:
        raise ServiceError("invalid_request", str(exc), 400) from exc
    return service.attention(thresholds)
