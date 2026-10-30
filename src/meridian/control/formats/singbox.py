"""sing-box JSON: `compiler.delivery.singbox_template` with its selector and urltest filled.

sing-box speaks no XHTTP, and it has no way to keep fingerprint copies out of its
urltest group, so both stay out of the document.
"""

from __future__ import annotations

import copy
from typing import Any

from meridian.compiler.delivery import singbox_template
from meridian.control.formats.capabilities import supported_endpoints
from meridian.control.formats.entries import (
    AUTOMATIC,
    PLACEHOLDER_ADDRESS,
    PLACEHOLDER_PORT,
    PLACEHOLDER_UUID,
    absolute,
    availability_placeholders,
    presentation_endpoints,
    tls_fingerprint,
)
from meridian.control.models import ClientEndpoint
from meridian.core.defaults import DEFAULT_CLIENT_ROUTING
from meridian.core.topology import ClientRoutingPolicy


def singbox_document(
    endpoints: list[ClientEndpoint],
    uuid: str,
    placeholders: tuple[str, ...] = (),
    *,
    client_routing: ClientRoutingPolicy = DEFAULT_CLIENT_ROUTING,
) -> dict[str, Any]:
    """Return the sing-box document for one user."""
    endpoints = supported_endpoints(presentation_endpoints(endpoints), "singbox")
    placeholders = availability_placeholders(endpoints, placeholders)
    if placeholders:
        proxies = [placeholder_outbound(remark) for remark in placeholders]
    else:
        proxies = [singbox_outbound(endpoint, uuid) for endpoint in endpoints]
    names = [proxy["tag"] for proxy in proxies]
    document = copy.deepcopy(singbox_template(client_routing=client_routing))
    for outbound in document["outbounds"]:
        if outbound["type"] == "selector":
            outbound["outbounds"] = [AUTOMATIC, *names]
        elif outbound["type"] == "urltest":
            outbound["outbounds"] = names
    document["outbounds"] = [*document["outbounds"], *proxies]
    return document


def singbox_outbound(endpoint: ClientEndpoint, uuid: str) -> dict[str, Any]:
    """Return one Reality, WSS, or Hysteria2 endpoint as a sing-box outbound."""
    outbound: dict[str, Any] = {
        "type": endpoint.protocol,
        "tag": endpoint.remark,
        "server": endpoint.address,
        "server_port": endpoint.port,
    }
    if endpoint.protocol == "hysteria2":
        return outbound | {
            "password": uuid,
            "tls": {"enabled": True, "server_name": endpoint.tls_sni, "alpn": ["h3"]},
            "obfs": {"type": "salamander", "password": endpoint.salamander_password},
        }
    if endpoint.protocol == "wss":
        transport: dict[str, Any] = {"type": "ws", "path": absolute(endpoint.path)}
        if endpoint.host:
            transport["headers"] = {"Host": endpoint.host}
        return outbound | {
            "type": "vless",
            "uuid": uuid,
            "transport": transport,
            "tls": {
                "enabled": True,
                "server_name": endpoint.tls_sni,
                "utls": {"enabled": True, "fingerprint": tls_fingerprint(endpoint)},
            },
        }
    tls: dict[str, Any] = {
        "enabled": True,
        "reality": {
            "enabled": True,
            "public_key": endpoint.reality_public_key,
            "short_id": endpoint.reality_short_id,
        },
        "server_name": endpoint.reality_sni,
        # sing-box refuses Reality without uTLS.
        "utls": {"enabled": True, "fingerprint": tls_fingerprint(endpoint)},
    }
    return outbound | {"type": "vless", "uuid": uuid, "flow": "xtls-rprx-vision", "tls": tls}


def placeholder_outbound(remark: str) -> dict[str, Any]:
    return {
        "type": "vless",
        "tag": remark,
        "server": PLACEHOLDER_ADDRESS,
        "server_port": PLACEHOLDER_PORT,
        "uuid": PLACEHOLDER_UUID,
    }
