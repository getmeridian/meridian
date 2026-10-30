"""HTTP subscription responses beside the control service's API routes."""

from __future__ import annotations

import re

from meridian.control.ui_routes import UiRequest


def subscription(request: UiRequest, match: re.Match[str]) -> None:
    response = request.ui.service.subscription(
        match[1],
        match[2] or "",
        user_agent=request.headers.get("User-Agent"),
        accept=request.headers.get("Accept"),
        device_headers=request.headers,
    )
    request.respond(response.status, response.content_type, response.body.encode("utf-8"), response.headers)
