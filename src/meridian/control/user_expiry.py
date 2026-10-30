"""Keep finite user expiries outside the legacy no-expiry sentinel range."""

from datetime import datetime

from meridian.control.errors import ServiceError
from meridian.core.client_limits import NEVER_EXPIRES_YEAR


def require_finite_user_expiry(expiry: str | None) -> str | None:
    """Return the expiry after refusing values client projections would mistake for no expiry."""
    if expiry is not None and datetime.fromisoformat(expiry).year >= NEVER_EXPIRES_YEAR:
        raise ServiceError(
            "invalid_request",
            f"Expiry must be before {NEVER_EXPIRES_YEAR}. Remove the expiry for access without an end date.",
            400,
        )
    return expiry
