"""Xray JSON: a list of full client configs, as Happ, v2rayNG, Streisand, and others import it.

The first config, "Automatic", puts every primary entry behind the health-checked
`MERIDIAN_AUTO` balancer (`compiler.delivery.xray_json_template`); fingerprint copies get
one "Automatic · <fingerprint>" config per fingerprint, so no balancer ever mixes fingerprints. A
suspended user gets one config per placeholder with no proxy at all (the balancer falls
back to BLOCK). `meridian test` reads the first config (`xray_client.parse_xray_subscription`).
"""

from __future__ import annotations

import copy
from typing import Any

from meridian.compiler.delivery import xray_json_template
from meridian.control.formats.entries import (
    AUTOMATIC,
    absolute,
    copy_groups,
    presentation_endpoints,
    primaries,
    tls_fingerprint,
)
from meridian.control.formats.links import finalmask
from meridian.control.models import ClientEndpoint

PROXY_TAG = "MERIDIAN_PROXY"


def xray_json_document(
    endpoints: list[ClientEndpoint], uuid: str, placeholders: tuple[str, ...] = ()
) -> list[dict[str, Any]]:
    """Return the Xray JSON document: one config per automatic group (or per placeholder)."""
    endpoints = presentation_endpoints(endpoints)
    if placeholders:
        return [_config([], uuid, remark) for remark in placeholders]
    configs = [_config(primaries(endpoints), uuid, AUTOMATIC)]
    configs += [_config(members, uuid, remark) for remark, members in copy_groups(endpoints)]
    return configs


def _config(endpoints: list[ClientEndpoint], uuid: str, remark: str) -> dict[str, Any]:
    config = {key: value for key, value in copy.deepcopy(xray_json_template()).items() if key != "remnawave"}
    proxies = [
        {"tag": PROXY_TAG if index == 1 else f"{PROXY_TAG}-{index}", **xray_outbound(endpoint, uuid)}
        for index, endpoint in enumerate(endpoints, start=1)
    ]
    config["outbounds"] = [*proxies, *config["outbounds"]]
    config["remarks"] = remark
    return config


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
                "finalmask": finalmask(endpoint),
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
        outbound["streamSettings"] = {
            "network": "tcp",
            "tcpSettings": {},
            "security": "reality",
            "realitySettings": {
                "serverName": endpoint.reality_sni,
                "publicKey": endpoint.reality_public_key,
                "shortId": endpoint.reality_short_id,
                "fingerprint": tls_fingerprint(endpoint),
            },
        }
        return outbound
    if endpoint.protocol == "wss":
        transport: dict[str, Any] = {
            "network": "ws",
            "wsSettings": {"path": absolute(endpoint.path), "host": endpoint.host, "headers": {}},
        }
    else:
        transport = {
            "network": "xhttp",
            "xhttpSettings": {"mode": "packet-up", "host": endpoint.host, "path": absolute(endpoint.path)},
        }
    outbound["streamSettings"] = {**transport, "security": "tls", "tlsSettings": _tls(endpoint)}
    return outbound


def _tls(endpoint: ClientEndpoint, *, alpn: list[str] | None = None) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "serverName": endpoint.tls_sni,
        "enableSessionResumption": False,
        "fingerprint": tls_fingerprint(endpoint),
    }
    if alpn:
        settings["alpn"] = alpn
    return settings
