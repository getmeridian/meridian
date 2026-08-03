"""Shared data models used across meridian modules.

Centralizes dataclasses that would otherwise create circular imports
between protocols.py and output.py.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProtocolURL:
    """A connection URL for a specific protocol.

    Used by output generation to iterate protocols generically
    instead of hardcoding reality/xhttp/wss fields.
    """

    key: str  # Protocol key: "reality", "xhttp", "wss"
    label: str  # Human-readable label: "Primary", "XHTTP", "CDN Backup"
    url: str  # Full connection URL (e.g., vless://...)
    qr_b64: str = ""  # Base64-encoded QR PNG (empty if not generated)


@dataclass(frozen=True)
class RelayURLSet:
    """Connection URLs for a client via a specific relay node.

    Groups the relay metadata with the Reality-only URLs that route
    through that relay.
    """

    relay_ip: str  # Relay node IP address
    relay_name: str  # Friendly name (e.g., "ru-moscow") or empty
    urls: list[ProtocolURL]  # Reality-only URLs with relay IP


def derive_client_name(protocol_urls: list[ProtocolURL], fallback: str = "client") -> str:
    """Derive client name from the first URL's fragment.

    URLs use the format ``vless://uuid@host:port?params#name``.
    Falls back to *fallback* if no fragment is present or the list is empty.
    """
    if not protocol_urls:
        return fallback
    frag = protocol_urls[0].url.rsplit("#", 1)
    return frag[-1] if len(frag) > 1 else fallback
