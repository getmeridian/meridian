"""The connection methods every exit offers by default.

Meridian publishes every current method that does not make the server easier to
detect, so a block on one leaves the others working: Reality always, XHTTP and
Hysteria2 on the domain or, without one, on the server's IPv4 address (Let's
Encrypt certifies IPs), and WebSocket only with a domain, since it exists to
ride a CDN.
"""

from __future__ import annotations

import ipaddress
import secrets
from collections.abc import Sequence
from typing import Literal

from meridian.core.defaults import DEFAULT_SNI
from meridian.core.topology import DEFAULT_TLS_FINGERPRINT, ProtocolPathIntent, TlsFingerprint


def default_exit_paths(
    exit_id: str,
    *,
    address: str,
    reality_sni: str = "",
    domain: str = "",
    fingerprint: TlsFingerprint | None = None,
    hysteria2: bool = True,
    previous: Sequence[ProtocolPathIntent] = (),
) -> list[ProtocolPathIntent]:
    """Return the exit's paths; a redeploy keeps its saved SNI, fingerprint, and transport paths."""
    prior = {path.protocol: path for path in previous}
    saved_reality = prior.get("reality")
    tls_fingerprint = fingerprint or (saved_reality.fingerprint if saved_reality else DEFAULT_TLS_FINGERPRINT)
    paths = [
        ProtocolPathIntent(
            id=f"{exit_id}-reality",
            protocol="reality",
            reality_sni=reality_sni or (saved_reality.reality_sni if saved_reality else DEFAULT_SNI),
            fingerprint=tls_fingerprint,
        )
    ]
    tls_host = domain or (address if _is_ipv4(address) else "")
    if not tls_host:
        return paths
    tls_protocols: tuple[Literal["xhttp", "wss"], ...] = ("xhttp", "wss") if domain else ("xhttp",)
    for protocol in tls_protocols:
        # Keep transport paths stable across redeploys so existing links survive.
        saved = prior.get(protocol)
        path = saved.path if saved is not None and saved.host == tls_host else secrets.token_hex(8)
        paths.append(
            ProtocolPathIntent(
                id=f"{exit_id}-{protocol}",
                protocol=protocol,
                tls_sni=tls_host,
                host=tls_host,
                path=path,
                fingerprint=tls_fingerprint,
            )
        )
    if hysteria2:
        paths.append(ProtocolPathIntent(id=f"{exit_id}-hysteria2", protocol="hysteria2", tls_sni=tls_host))
    return paths


def _is_ipv4(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).version == 4
    except ValueError:
        return False
