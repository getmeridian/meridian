"""Share links (`vless://`, `hysteria2://`) and the Base64 document that lists them.

Base64 has no failover: clients get every entry, fingerprint copies included, in view order.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import quote, urlencode

from meridian.control.formats.entries import (
    PLACEHOLDER_ADDRESS,
    PLACEHOLDER_PORT,
    PLACEHOLDER_UUID,
    absolute,
    availability_placeholders,
    presentation_endpoints,
)
from meridian.control.models import ClientEndpoint


def links(endpoints: list[ClientEndpoint], uuid: str, placeholders: tuple[str, ...] = ()) -> list[str]:
    """Every entry's link in view order, or only the placeholders when there are any."""
    endpoints = presentation_endpoints(endpoints)
    placeholders = availability_placeholders(endpoints, placeholders)
    if placeholders:
        return [placeholder_uri(remark) for remark in placeholders]
    return [share_uri(endpoint, uuid) for endpoint in endpoints]


def base64_document(endpoints: list[ClientEndpoint], uuid: str, placeholders: tuple[str, ...] = ()) -> str:
    """The Base64 document: the links one per line (no trailing newline), standard Base64."""
    return base64.b64encode("\n".join(links(endpoints, uuid, placeholders)).encode("utf-8")).decode("ascii")


def share_uri(endpoint: ClientEndpoint, uuid: str) -> str:
    """Return the `vless://` (Reality, XHTTP, WSS) or `hysteria2://` URI of one endpoint."""
    authority = f"{quote(uuid, safe='')}@{_uri_host(endpoint.address)}:{endpoint.port}"
    fragment = quote(endpoint.remark, safe="")
    if endpoint.protocol == "hysteria2":
        query = urlencode(
            {
                "obfs": "salamander",
                "obfs-password": endpoint.salamander_password,
                "sni": endpoint.tls_sni,
                "fm": json.dumps(finalmask(endpoint), separators=(",", ":")),
            },
            quote_via=quote,
        )
        return f"hysteria2://{authority}/?{query}#{fragment}"
    params: dict[str, str] = {"encryption": "none"}
    if endpoint.protocol == "reality":
        params |= {
            "flow": "xtls-rprx-vision",
            "type": "tcp",
            "security": "reality",
            "sni": endpoint.reality_sni,
            "fp": endpoint.fingerprint,
            "pbk": endpoint.reality_public_key,
            "sid": endpoint.reality_short_id,
        }
    else:
        # Remnawave's order: transport (type, path, host, XHTTP's mode), then security.
        params |= {"type": "ws" if endpoint.protocol == "wss" else "xhttp", "path": absolute(endpoint.path)}
        params |= {"host": endpoint.host, "mode": "packet-up" if endpoint.protocol == "xhttp" else ""}
        params |= {"security": "tls", "sni": endpoint.tls_sni, "fp": endpoint.fingerprint}
    query = urlencode({key: value for key, value in params.items() if value}, quote_via=quote)
    return f"vless://{authority}?{query}#{fragment}"


def placeholder_uri(remark: str) -> str:
    """A link that names a status and connects nowhere."""
    return (
        f"vless://{PLACEHOLDER_UUID}@{PLACEHOLDER_ADDRESS}:{PLACEHOLDER_PORT}"
        f"?encryption=none&type=tcp&security=none#{quote(remark, safe='')}"
    )


def finalmask(endpoint: ClientEndpoint) -> dict[str, Any]:
    """Hysteria2's salamander obfuscation as Xray's `finalmask` (also carried in the link's `fm`)."""
    return {"udp": [{"type": "salamander", "settings": {"password": endpoint.salamander_password}}]}


def _uri_host(address: str) -> str:
    return f"[{address}]" if ":" in address else address
