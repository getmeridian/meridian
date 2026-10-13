"""Shared data models used across meridian modules."""

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
