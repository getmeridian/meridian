"""Best-effort country of a server, from its Regional Internet Registry record.

Runs once on the deployer's machine when an exit is added. IANA's RDAP bootstrap
file (RFC 9224) names the registry responsible for the address, and that
registry's RDAP record carries the country the block is registered in. No
commercial geo-IP service sees the address. Allocation country is not always
where the machine sits (anycast, leased ranges), so callers let `--country`
override it and treat an empty answer as "unknown".
"""

from __future__ import annotations

import ipaddress
import logging
import socket

import httpx

from meridian.core.inputs import validate_country_code_value

logger = logging.getLogger(__name__)

_BOOTSTRAP = {4: "https://data.iana.org/rdap/ipv4.json", 6: "https://data.iana.org/rdap/ipv6.json"}
_TIMEOUT = 5.0
# Used when a caller passes no transport; the test suite points it at a refusing mock.
DEFAULT_TRANSPORT: httpx.BaseTransport | None = None


def registry_country(address: str, *, timeout: float = _TIMEOUT, transport: httpx.BaseTransport | None = None) -> str:
    """The two-letter country *address* is registered in, or ``""`` when unknown."""
    try:
        ip = _ip(address)
        with httpx.Client(timeout=timeout, follow_redirects=True, transport=transport or DEFAULT_TRANSPORT) as client:
            base = _registry_base(client, ip)
            if not base:
                return ""
            response = client.get(f"{base.rstrip('/')}/ip/{ip}", headers={"Accept": "application/rdap+json"})
            response.raise_for_status()
            return validate_country_code_value(str(response.json().get("country") or ""))
    except (OSError, ValueError, httpx.HTTPError) as exc:
        logger.debug("Registry country lookup for %s failed: %s", address, exc)
        return ""


def _ip(address: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        return ipaddress.ip_address(address)
    except ValueError:
        resolved = socket.getaddrinfo(address, None, proto=socket.IPPROTO_TCP)[0][4][0]
        return ipaddress.ip_address(resolved)


def _registry_base(client: httpx.Client, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    if not ip.is_global:
        return ""
    response = client.get(_BOOTSTRAP[ip.version])
    response.raise_for_status()
    best: tuple[int, str] = (-1, "")
    for prefixes, urls in response.json().get("services", []):
        https = next((url for url in urls if url.startswith("https://")), "")
        for prefix in prefixes:
            network = ipaddress.ip_network(prefix, strict=False)
            if ip in network and network.prefixlen > best[0] and https:
                best = (network.prefixlen, https)
    return best[1]
