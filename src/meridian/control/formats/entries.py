"""What every subscription format shares: entry order, fingerprint copies, placeholders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from meridian.compiler.names import fingerprint_label
from meridian.control.models import ENDPOINT_REMARK_MAX_LENGTH, ClientEndpoint, LimitStrategy

UserStatus = Literal["active", "disabled", "limited", "expired"]

# A suspended user's subscription lists these entries in place of real ones, so the app
# itself says why nothing connects (the wording Remnawave users already know).
STATUS_REMARKS: dict[UserStatus, tuple[str, ...]] = {
    "active": (),
    "disabled": ("🚫 Subscription disabled", "Contact support"),
    "limited": ("🚧 Subscription limited", "Contact support"),
    "expired": ("⌛ Subscription expired", "Contact support"),
}
NO_CONNECTIONS_REMARKS = (
    "No connections available yet",
    "Подключения пока недоступны",
    "هنوز هیچ اتصالی در دسترس نیست",
    "暂时没有可用连接",
)
# Placeholders point at 0.0.0.0:1 with a credential nobody holds; the support page
# finds no connection page under it and says the page is unavailable.
PLACEHOLDER_UUID = "00000000-0000-0000-0000-000000000000"
PLACEHOLDER_ADDRESS = "0.0.0.0"
PLACEHOLDER_PORT = 1
# Remnawave filled an empty TLS fingerprint (Hysteria2 carries none) with Chrome's in
# Xray JSON and Mihomo; Base64 links keep it empty, and sing-box Reality needs one too.
DEFAULT_FINGERPRINT = "chrome"
AUTOMATIC = "Automatic"
RESERVED_NAMES = frozenset({AUTOMATIC, "Meridian", "direct", "DIRECT", "REJECT"})


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
    limit_strategy: LimitStrategy = "no_reset"

    @property
    def placeholders(self) -> tuple[str, ...]:
        """The entries a suspended user sees instead of real ones (none when active)."""
        return STATUS_REMARKS[self.status]


def ordered(endpoints: list[ClientEndpoint]) -> list[ClientEndpoint]:
    """Endpoints in view order: the order every format lists them in."""
    return sorted(endpoints, key=lambda endpoint: (endpoint.position, endpoint.remark, endpoint.model_dump_json()))


def presentation_endpoints(endpoints: list[ClientEndpoint]) -> list[ClientEndpoint]:
    """Unique, bounded display names in stable order, without changing stored endpoints."""
    listed = ordered(endpoints)
    reserved = RESERVED_NAMES | {remark for remark, _ in copy_groups(listed)}
    original_names = {endpoint.remark for endpoint in listed}
    used = set(reserved)
    next_suffix: dict[str, int] = {}
    result = []
    for endpoint in listed:
        name = endpoint.remark
        if name in used:
            index = next_suffix.get(name, 2)
            while True:
                suffix = f" ({index})"
                candidate = name[: ENDPOINT_REMARK_MAX_LENGTH - len(suffix)] + suffix
                if candidate not in used and candidate not in original_names:
                    break
                index += 1
            next_suffix[name] = index + 1
            name = candidate
        used.add(name)
        result.append(endpoint if name == endpoint.remark else endpoint.model_copy(update={"remark": name}))
    return result


def availability_placeholders(endpoints: list[ClientEndpoint], placeholders: tuple[str, ...]) -> tuple[str, ...]:
    """Keep status notices, or explain an empty set of supported connections."""
    return placeholders or (() if endpoints else NO_CONNECTIONS_REMARKS)


def primaries(endpoints: list[ClientEndpoint]) -> list[ClientEndpoint]:
    """Primary entries, preserving the caller's presentation order."""
    return [endpoint for endpoint in endpoints if not endpoint.fingerprint_copy]


def copies(endpoints: list[ClientEndpoint]) -> list[ClientEndpoint]:
    """Fingerprint copies, preserving the caller's presentation order."""
    return [endpoint for endpoint in endpoints if endpoint.fingerprint_copy]


def copy_groups(endpoints: list[ClientEndpoint]) -> list[tuple[str, list[ClientEndpoint]]]:
    """Fingerprint copies grouped by fingerprint, in view order, each with its automatic entry's remark.

    A path whose primary is neither copy fingerprint (Safari) has two copies; one group holding
    both would switch fingerprints toward the same server, which TSPU escalates.
    """
    groups: dict[str, list[ClientEndpoint]] = {}
    for endpoint in copies(endpoints):
        groups.setdefault(fingerprint_label(endpoint.fingerprint), []).append(endpoint)
    return [(f"{AUTOMATIC} · {label}", members) for label, members in groups.items()]


def tls_fingerprint(endpoint: ClientEndpoint) -> str:
    return endpoint.fingerprint or DEFAULT_FINGERPRINT


def absolute(path: str) -> str:
    return path if path.startswith("/") else f"/{path}"
