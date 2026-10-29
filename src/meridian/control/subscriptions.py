"""Subscription documents the control service renders for one user.

Shapes follow the golden corpus captured from Remnawave (`tests/systemlab/golden/`):
URI lines for Base64 clients and one "Automatic" Xray JSON config whose
`MERIDIAN_PROXY*` outbounds feed the health-checked balancer.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import quote, urlencode

from meridian.compiler.delivery import xray_json_template
from meridian.control.models import ClientEndpoint

PROXY_TAG = "MERIDIAN_PROXY"


def base64_subscription(endpoints: list[ClientEndpoint], uuid: str) -> str:
    """Return the Base64 document: one share URI per endpoint, in view order."""
    lines = [share_uri(endpoint, uuid) for endpoint in _ordered(endpoints)]
    return base64.b64encode("\n".join(lines).encode("utf-8")).decode("ascii")


def xray_json_subscription(endpoints: list[ClientEndpoint], uuid: str) -> list[dict[str, Any]]:
    """Return the Xray JSON document: the Automatic config with every endpoint as a proxy outbound."""
    config = {key: value for key, value in xray_json_template().items() if key != "remnawave"}
    proxies = [
        {**xray_outbound(endpoint, uuid), "tag": PROXY_TAG if index == 1 else f"{PROXY_TAG}-{index}"}
        for index, endpoint in enumerate(_ordered(endpoints), start=1)
    ]
    config["outbounds"] = [*proxies, *config["outbounds"]]
    config["remarks"] = "Automatic"
    return [config]


def share_uri(endpoint: ClientEndpoint, uuid: str) -> str:
    """Return the `vless://` or `hysteria2://` URI of one endpoint."""
    authority = f"{quote(uuid, safe='')}@{_uri_host(endpoint.address)}:{endpoint.port}"
    fragment = quote(endpoint.remark, safe="")
    if endpoint.protocol == "hysteria2":
        finalmask = json.dumps(_finalmask(endpoint), separators=(",", ":"))
        query = urlencode(
            {
                "obfs": "salamander",
                "obfs-password": endpoint.salamander_password,
                "sni": endpoint.tls_sni,
                "fm": finalmask,
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
        params |= {
            "type": "xhttp",
            "path": _absolute(endpoint.path),
            "host": endpoint.host,
            "mode": "packet-up",
            "security": "tls",
            "sni": endpoint.tls_sni,
            "fp": endpoint.fingerprint,
        }
    query = urlencode({key: value for key, value in params.items() if value}, quote_via=quote)
    return f"vless://{authority}?{query}#{fragment}"


def xray_outbound(endpoint: ClientEndpoint, uuid: str) -> dict[str, Any]:
    """Return the Xray client outbound of one endpoint (without its tag)."""
    if endpoint.protocol == "hysteria2":
        return {
            "protocol": "hysteria",
            "settings": {"address": endpoint.address, "port": endpoint.port, "version": 2},
            "streamSettings": {
                "network": "hysteria",
                "hysteriaSettings": {"version": 2, "auth": uuid},
                "security": "tls",
                "tlsSettings": _tls(endpoint, alpn=["h3"]),
                "finalmask": _finalmask(endpoint),
            },
        }
    flow = "xtls-rprx-vision" if endpoint.protocol == "reality" else ""
    outbound: dict[str, Any] = {
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": endpoint.address,
                    "port": endpoint.port,
                    "users": [{"id": uuid, "encryption": "none", "flow": flow}],
                }
            ]
        },
    }
    if endpoint.protocol == "reality":
        reality = {
            "serverName": endpoint.reality_sni,
            "publicKey": endpoint.reality_public_key,
            "shortId": endpoint.reality_short_id,
        }
        if endpoint.fingerprint:
            reality["fingerprint"] = endpoint.fingerprint
        outbound["streamSettings"] = {
            "network": "tcp",
            "tcpSettings": {},
            "security": "reality",
            "realitySettings": reality,
        }
        return outbound
    outbound["streamSettings"] = {
        "network": "xhttp",
        "xhttpSettings": {"mode": "packet-up", "host": endpoint.host, "path": _absolute(endpoint.path)},
        "security": "tls",
        "tlsSettings": _tls(endpoint),
    }
    return outbound


def _tls(endpoint: ClientEndpoint, *, alpn: list[str] | None = None) -> dict[str, Any]:
    settings: dict[str, Any] = {"serverName": endpoint.tls_sni, "enableSessionResumption": False}
    if endpoint.fingerprint:
        settings["fingerprint"] = endpoint.fingerprint
    if alpn:
        settings["alpn"] = alpn
    return settings


def _finalmask(endpoint: ClientEndpoint) -> dict[str, Any]:
    return {"udp": [{"type": "salamander", "settings": {"password": endpoint.salamander_password}}]}


def _ordered(endpoints: list[ClientEndpoint]) -> list[ClientEndpoint]:
    return sorted(endpoints, key=lambda endpoint: (endpoint.position, endpoint.remark))


def _absolute(path: str) -> str:
    return path if path.startswith("/") else f"/{path}"


def _uri_host(address: str) -> str:
    return f"[{address}]" if ":" in address else address
