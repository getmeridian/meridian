"""What every subscription format shares: entry order, fingerprint copies, placeholders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from meridian.compiler.names import fingerprint_label
from meridian.control.models import ClientEndpoint

UserStatus = Literal["active", "disabled", "limited", "expired"]

# A suspended user's subscription lists these entries in place of real ones, so the app
# itself says why nothing connects (the wording Remnawave users already know).
STATUS_REMARKS: dict[UserStatus, tuple[str, ...]] = {
    "active": (),
    "disabled": ("🚫 Subscription disabled", "Contact support"),
    "limited": ("🚧 Subscription limited", "Contact support"),
    "expired": ("⌛ Subscription expired", "Contact support"),
}
# Placeholders point at 0.0.0.0:1 with a credential nobody holds; the support page
# finds no connection page under it and says the page is unavailable.
PLACEHOLDER_UUID = "00000000-0000-0000-0000-000000000000"
PLACEHOLDER_ADDRESS = "0.0.0.0"
PLACEHOLDER_PORT = 1
# Remnawave filled an empty TLS fingerprint (Hysteria2 carries none) with Chrome's in
# Xray JSON and Mihomo; Base64 links keep it empty, and sing-box Reality needs one too.
DEFAULT_FINGERPRINT = "chrome"
AUTOMATIC = "Automatic"


@dataclass(frozen=True)
class SubscriptionUser:
    """What a subscription needs to know about its user, as plain values.

    `expires_at` is timezone-aware (None never expires); traffic counts are bytes, and a
    `limit_bytes` of 0 means unlimited. `short_id` is the token in the subscription URL.
    """

    username: str
    uuid: str
    short_id: str
    status: UserStatus = "active"
    expires_at: datetime | None = None
    used_bytes: int = 0
    limit_bytes: int = 0
    lifetime_used_bytes: int = 0

    @property
    def placeholders(self) -> tuple[str, ...]:
        """The entries a suspended user sees instead of real ones (none when active)."""
        return STATUS_REMARKS[self.status]


def ordered(endpoints: list[ClientEndpoint]) -> list[ClientEndpoint]:
    """Endpoints in view order: the order every format lists them in."""
    return sorted(endpoints, key=lambda endpoint: (endpoint.position, endpoint.remark))


def primaries(endpoints: list[ClientEndpoint]) -> list[ClientEndpoint]:
    return [endpoint for endpoint in ordered(endpoints) if not endpoint.fingerprint_copy]


def copies(endpoints: list[ClientEndpoint]) -> list[ClientEndpoint]:
    return [endpoint for endpoint in ordered(endpoints) if endpoint.fingerprint_copy]


def copies_remark(endpoints: list[ClientEndpoint]) -> str:
    """Remark of the copies' own automatic entry: their fingerprint, or "Backup" when they mix."""
    labels = sorted({fingerprint_label(endpoint.fingerprint) for endpoint in copies(endpoints)})
    return f"{AUTOMATIC} · {labels[0] if len(labels) == 1 else 'Backup'}"


def tls_fingerprint(endpoint: ClientEndpoint) -> str:
    return endpoint.fingerprint or DEFAULT_FINGERPRINT


def absolute(path: str) -> str:
    return path if path.startswith("/") else f"/{path}"
