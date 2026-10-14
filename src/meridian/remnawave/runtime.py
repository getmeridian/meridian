"""Typed Remnawave errors raised by the REST adapter."""

from __future__ import annotations

from meridian.core.errors import MeridianError


class RemnawaveError(MeridianError):
    """Raised when a Remnawave API call fails."""


class RemnawaveNotFoundError(RemnawaveError):
    """Resource not found (404)."""


class RemnawaveAuthError(RemnawaveError):
    """Authentication or authorization failure (401/403)."""


class RemnawaveNetworkError(RemnawaveError):
    """Transport-level failure (connection refused, timeout)."""
