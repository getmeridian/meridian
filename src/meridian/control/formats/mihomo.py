"""Mihomo (Clash Meta) YAML, and the Stash and plain-Clash variants of the same document.

`compiler.delivery.mihomo_template` is the reviewed document: a "Meridian" selector over
"Automatic" and every entry, and an ordered, health-checked "Automatic" fallback group
whose `exclude-filter` keeps fingerprint copies out. The template's proxy lists are filled
here, as Remnawave filled them.

Stash speaks the same YAML with gaps, so its variant differs (per stash.wiki): no XHTTP
(added only in Stash 3.6), no `exclude-filter` (the fallback group lists only primary
entries instead), no `GEOIP,private` (Stash names private ranges `LAN`), and Stash's own
field names: `sni` for the TLS name and `auth` for the Hysteria2 password.
"""

from __future__ import annotations

from typing import Any

import yaml

from meridian.compiler.delivery import mihomo_template
from meridian.control.formats.entries import (
    AUTOMATIC,
    DEFAULT_FINGERPRINT,
    PLACEHOLDER_ADDRESS,
    PLACEHOLDER_PORT,
    PLACEHOLDER_UUID,
    absolute,
    copies,
    presentation_endpoints,
    tls_fingerprint,
)
from meridian.control.models import ClientEndpoint

_SELECT_GROUP = "Meridian"
_STASH_PRIVATE_RULE = ("GEOIP,private,", "GEOIP,LAN,")
# Stash's documented names for fields Mihomo spells otherwise.
_STASH_FIELDS = {"servername": "sni", "password": "auth"}
_NETWORKS = {"reality": "tcp", "xhttp": "xhttp", "wss": "ws"}


def mihomo_document(
    endpoints: list[ClientEndpoint],
    uuid: str,
    placeholders: tuple[str, ...] = (),
    *,
    stash: bool = False,
) -> str:
    """Return the Mihomo document, or its Stash variant."""
    endpoints = presentation_endpoints(endpoints)
    exclude = sorted(endpoint.remark for endpoint in copies(endpoints))
    document: dict[str, Any] = yaml.safe_load(mihomo_template(exclude_from_failover=exclude))
    if placeholders:
        proxies = [placeholder_proxy(remark) for remark in placeholders]
    else:
        proxies = [mihomo_proxy(endpoint, uuid) for endpoint in endpoints]
    names = [proxy["name"] for proxy in proxies]
    document["proxies"] = proxies
    for group in document["proxy-groups"]:
        if group["name"] == _SELECT_GROUP:
            group["proxies"] = [*(group["proxies"] or []), *names]
        elif group["name"] == AUTOMATIC:
            group["proxies"] = names
    if stash:
        _to_stash(document, [] if placeholders else endpoints)
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=1_000_000)


def _to_stash(document: dict[str, Any], listed: list[ClientEndpoint]) -> None:
    """Rewrite a filled Mihomo document in Stash's dialect (*listed*: the real entries in it)."""
    xhttp = {endpoint.remark for endpoint in listed if endpoint.protocol == "xhttp"}
    skipped = xhttp | {endpoint.remark for endpoint in copies(listed)}
    document["proxies"] = [
        {_STASH_FIELDS.get(key, key): value for key, value in proxy.items()}
        for proxy in document["proxies"]
        if proxy["name"] not in xhttp
    ]
    for group in document["proxy-groups"]:
        dropped = skipped if group.pop("exclude-filter", None) is not None else xhttp
        group["proxies"] = [name for name in group["proxies"] if name not in dropped]
    old, new = _STASH_PRIVATE_RULE
    document["rules"] = [new + rule.removeprefix(old) if rule.startswith(old) else rule for rule in document["rules"]]


def mihomo_proxy(endpoint: ClientEndpoint, uuid: str) -> dict[str, Any]:
    """Return one endpoint as a Mihomo proxy."""
    proxy: dict[str, Any] = {"name": endpoint.remark, "type": "", "server": endpoint.address, "port": endpoint.port}
    if endpoint.protocol == "hysteria2":
        return proxy | {
            "type": "hysteria2",
            "udp": True,
            "password": uuid,
            "obfs": "salamander",
            "obfs-password": endpoint.salamander_password,
            "sni": endpoint.tls_sni,
            "client-fingerprint": tls_fingerprint(endpoint),
            "alpn": ["h3"],
        }
    network = _NETWORKS[endpoint.protocol]
    proxy |= {"type": "vless", "network": network, "udp": True, "uuid": uuid, "packet-encoding": "xudp"}
    if endpoint.protocol == "reality":
        proxy |= {
            "flow": "xtls-rprx-vision",
            "tls": True,
            "servername": endpoint.reality_sni,
            "reality-opts": {"public-key": endpoint.reality_public_key, "short-id": endpoint.reality_short_id},
        }
    elif endpoint.protocol == "wss":
        proxy |= {
            "tls": True,
            "servername": endpoint.tls_sni,
            # Remnawave sends the WebSocket Host as a header, as Mihomo documents it.
            "ws-opts": {"path": absolute(endpoint.path), "headers": {"Host": endpoint.host} if endpoint.host else {}},
        }
    else:
        proxy |= {
            "tls": True,
            "servername": endpoint.tls_sni,
            "xhttp-opts": {"path": absolute(endpoint.path), "host": endpoint.host, "mode": "packet-up"},
        }
    return proxy | {"client-fingerprint": tls_fingerprint(endpoint)}


def placeholder_proxy(remark: str) -> dict[str, Any]:
    return {
        "name": remark,
        "type": "vless",
        "server": PLACEHOLDER_ADDRESS,
        "port": PLACEHOLDER_PORT,
        "network": "tcp",
        "udp": True,
        "uuid": PLACEHOLDER_UUID,
        "packet-encoding": "xudp",
        "client-fingerprint": DEFAULT_FINGERPRINT,
    }
