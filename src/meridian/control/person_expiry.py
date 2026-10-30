"""Read-only person renewal previews, using core's calendar and expiry contract."""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

from meridian.control.errors import ServiceError
from meridian.control.times import utc_now
from meridian.core.client_limits import (
    NEVER_EXPIRES_YEAR,
    CalendarExpiryPreview,
    CalendarRenewalRequest,
    ClientLimitError,
    preview_calendar_expiry,
)

if TYPE_CHECKING:
    from meridian.control.ui_routes import UiRequest


def expiry_preview(request: UiRequest, match: re.Match[str]) -> CalendarExpiryPreview:
    """Preview only; the person form PATCHes the chosen instant when the owner saves."""
    query = parse_qs(urlsplit(request.path).query, keep_blank_values=True)
    values = query.get("months", [])
    if set(query) != {"months"} or len(values) != 1 or values[0] not in {"1", "3"}:
        raise ServiceError("invalid_request", "Choose +1 month or +3 months.", 400)
    renewal = CalendarRenewalRequest(months=int(values[0]))
    user = request.ui.service.get_user(match[1])
    if user is None:
        raise ServiceError("not_found", "Unknown user.", 404)
    try:
        return preview_calendar_expiry(user.expire_at, renewal, now=datetime.fromisoformat(utc_now()))
    except ClientLimitError as exc:
        raise ServiceError(
            "expiry_too_far", f"Renewal must end before {NEVER_EXPIRES_YEAR}. Choose no expiry instead.", 400
        ) from exc
