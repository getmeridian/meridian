"""Comparable projections of Remnawave panel records for driver observations."""

from __future__ import annotations

from typing import Any

from meridian.compiler.models import InboundPayload
from meridian.remnawave import ConfigProfile, Host, Inbound, InternalSquad, Node


def profile_projection(profile: ConfigProfile) -> dict[str, Any]:
    return {"name": profile.name, "config": profile.config}


def inbound_projection(inbound: Inbound) -> dict[str, Any]:
    return {
        "tag": inbound.tag,
        "type": inbound.type,
        "network": inbound.network,
        "security": inbound.security,
        "profile_uuid": inbound.profile_uuid,
        "port": inbound.port,
    }


def inbound_matches(inbound: Inbound, payload: InboundPayload, profile_uuid: str) -> bool:
    expected = {
        "reality": ("vless", "tcp", "reality"),
        "xhttp": ("vless", "xhttp", "none"),
        "wss": ("vless", "ws", "none"),
        "hysteria2": ("hysteria", "hysteria", "tls"),
    }[payload.protocol]
    return (
        inbound.tag == payload.tag
        and (inbound.type, inbound.network, inbound.security) == expected
        and inbound.port == payload.listen_port
        and inbound.profile_uuid in {"", profile_uuid}
    )


def node_projection(node: Node) -> dict[str, Any]:
    return {
        "name": node.name,
        "address": node.address,
        "port": node.port,
        "is_disabled": node.is_disabled,
        "profile_uuid": node.active_config_profile_uuid,
        "inbound_uuids": sorted(node.active_inbound_uuids),
    }


def host_projection(host: Host) -> dict[str, Any]:
    return {
        "remark": host.remark,
        "address": host.address,
        "port": host.port,
        "config_profile_uuid": host.config_profile_uuid,
        "inbound_uuid": host.inbound_uuid,
        "sni": host.sni,
        "host": host.host,
        "path": host.path,
        "alpn": host.alpn,
        "fingerprint": host.fingerprint,
        "security_layer": host.security_layer,
        "is_disabled": host.is_disabled,
        "tags": sorted(host.tags),
        "is_hidden": host.is_hidden,
        "xray_json_template_uuid": host.xray_json_template_uuid,
        "exclude_from_subscription_types": sorted(host.exclude_from_subscription_types),
        "view_position": host.view_position,
    }


def squad_projection(squad: InternalSquad) -> dict[str, Any]:
    return {
        "name": squad.name,
        "inbound_uuids": sorted(squad.inbound_uuids),
    }
