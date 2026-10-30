"""Parse a delivered subscription into labelled connection links and local QR codes."""

from __future__ import annotations

import base64
import binascii
from urllib.parse import parse_qs, unquote, urlsplit

from meridian.models import ProtocolURL

_PROTOCOL_LABELS = {"reality": "Primary", "xhttp": "XHTTP", "wss": "CDN Backup", "hysteria2": "UDP fallback"}


def protocol_key(url: str) -> str:
    """Classify one subscription link into a Meridian protocol key."""
    parsed = urlsplit(url)
    if parsed.scheme in {"hysteria2", "hy2"}:
        return "hysteria2"
    params = parse_qs(parsed.query)
    if params.get("security") == ["reality"]:
        return "reality"
    if params.get("type") == ["xhttp"]:
        return "xhttp"
    if params.get("type") == ["ws"]:
        return "wss"
    return ""


def subscription_protocol_urls(content: str) -> list[ProtocolURL]:
    """Parse a base64 (or plain) subscription body into labelled links with QR codes."""
    from meridian.urls import generate_qr_base64

    text = content.strip()
    try:
        decoded = base64.b64decode(text + "=" * (-len(text) % 4), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        decoded = text
    links: list[ProtocolURL] = []
    for line in decoded.splitlines():
        url = line.strip()
        key = protocol_key(url) if "://" in url else ""
        if not key:
            continue
        label = unquote(urlsplit(url).fragment) or _PROTOCOL_LABELS[key]
        links.append(ProtocolURL(key=key, label=label, url=url, qr_b64=generate_qr_base64(url)))
    return links
