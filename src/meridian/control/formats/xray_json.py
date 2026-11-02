"""Xray JSON: a list of full client configs, as Happ, v2rayNG, Streisand, and others import it.

The first config, "Automatic", puts every primary entry behind the health-checked
`MERIDIAN_AUTO` balancer (`compiler.delivery.xray_json_template`); each primary then gets
its own named config with one eligible proxy and the same routing. Fingerprint copies get
one "Automatic · <fingerprint>" config per fingerprint, so no balancer ever mixes fingerprints. A
suspended user gets one config per placeholder with no proxy at all (the balancer falls
back to BLOCK). `meridian test` reads the first config (`xray_client.parse_xray_subscription`).
"""

from __future__ import annotations

import copy
from typing import Any

from meridian.compiler.delivery import xray_json_template
from meridian.control.formats.delivery_policy import apply_xray_policy
from meridian.control.formats.entries import (
    AUTOMATIC,
    absolute,
    availability_placeholders,
    copy_groups,
    presentation_endpoints,
    primaries,
    tls_fingerprint,
    trailing_notices,
)
from meridian.control.formats.links import finalmask
from meridian.control.models import ClientEndpoint
from meridian.core.defaults import DEFAULT_CLIENT_ROUTING
from meridian.core.delivery_policies import ResolvedDeliveryPolicy
from meridian.core.topology import ClientRoutingPolicy

PROXY_TAG = "MERIDIAN_PROXY"


def xray_json_document(
    endpoints: list[ClientEndpoint],
    uuid: str,
    placeholders: tuple[str, ...] = (),
    *,
    client_routing: ClientRoutingPolicy = DEFAULT_CLIENT_ROUTING,
    notices: tuple[str, ...] = (),
    delivery_policy: ResolvedDeliveryPolicy | None = None,
) -> list[dict[str, Any]]:
    """Automatic first, individual primary profiles, then isolated copy groups (or placeholders).

    *notices* become proxy-less configs after the last real one, unless placeholders replace them.
    """
    if delivery_policy is not None:
        client_routing = delivery_policy.policy.preset
    endpoints = presentation_endpoints(endpoints)
    placeholders = availability_placeholders(primaries(endpoints), placeholders)
    if placeholders:
        return [_config([], uuid, remark, client_routing, delivery_policy) for remark in placeholders]
    primary = primaries(endpoints)
    configs = [_config(primary, uuid, AUTOMATIC, client_routing, delivery_policy)]
    configs += [_config([endpoint], uuid, endpoint.remark, client_routing, delivery_policy) for endpoint in primary]
    configs += [
        _config(members, uuid, remark, client_routing, delivery_policy) for remark, members in copy_groups(endpoints)
    ]
    inert = trailing_notices(endpoints, placeholders, notices)
    configs += [_config([], uuid, remark, client_routing, delivery_policy) for remark in inert]
    return configs


def _config(
    endpoints: list[ClientEndpoint],
    uuid: str,
    remark: str,
    client_routing: ClientRoutingPolicy,
    delivery_policy: ResolvedDeliveryPolicy | None,
) -> dict[str, Any]:
    config = {
        key: value
        for key, value in copy.deepcopy(xray_json_template(client_routing=client_routing)).items()
        if key != "remnawave"
    }
    custom = delivery_policy is not None and not delivery_policy.built_in
    proxies = [
        {
            "tag": f"{PROXY_TAG}-{index}." if custom else PROXY_TAG if index == 1 else f"{PROXY_TAG}-{index}",
            **xray_outbound(endpoint, uuid),
        }
        for index, endpoint in enumerate(endpoints, start=1)
    ]
    config["outbounds"] = [*proxies, *config["outbounds"]]
    if custom:
        assert delivery_policy is not None
        apply_xray_policy(
            config,
            delivery_policy,
            endpoints,
            {id(endpoint): proxy["tag"] for endpoint, proxy in zip(endpoints, proxies, strict=True)},
        )
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
