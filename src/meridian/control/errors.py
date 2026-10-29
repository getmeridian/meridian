"""Refusals the control service answers with, keyed by the agent protocol's error codes."""

from __future__ import annotations

from meridian.core.agent_http import AGENT_HTTP_SURFACE

_STATUS: dict[str, int] = {item.code: item.http_status for item in AGENT_HTTP_SURFACE.errors}


class ServiceError(Exception):
    """A refused request: `code` is an agent error code, `status` its HTTP status."""

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def agent_error(code: str, message: str) -> ServiceError:
    """A refusal with the HTTP status the transport contract maps `code` to."""
    return ServiceError(code, message, _STATUS.get(code, 400))
