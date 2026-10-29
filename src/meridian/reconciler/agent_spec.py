"""Render an exit's or routing gateway's reviewed Xray profile into the node spec its agent runs.

The spec pins the Xray archive (binary plus both geodata files from the same
release), the server config without users, and the systemd unit that restarts
on any of them. The control service adds one users resource per client inbound.
A gateway bridge on an exit is no client inbound: its one client is the gateway,
with an ID derived from the exit's keys (`bridge_client_uuid`), rendered here.
"""

from __future__ import annotations

from typing import Any, Final

from meridian.cluster import RealityKeyBinding
from meridian.compiler.models import ConfigProfilePayload
from meridian.config import XRAY_ARCHIVE_BYTES, XRAY_GITHUB_URL, XRAY_SHA256, XRAY_VERSION
from meridian.control.models import ManagedInbound, NodeSpec, users_resource_id
from meridian.host_runtime import AGENT_LIB_DIR, CORE_BINARY, CORE_CONFIG, CORE_UNIT
from meridian.xray_workload import ServiceRouteCredential, bridge_client_uuid, render_workload_config

XRAY_LINUX_ASSET = "Xray-linux-64.zip"
# Loopback gRPC API the agent will use for users and traffic counters (M2).
CORE_API_PORT = 10085
_PINS = (
    ("core-bin", "xray", "xray", CORE_BINARY, "0755"),
    ("core-geoip", "geoip", "geoip.dat", f"{AGENT_LIB_DIR}/geoip.dat", "0644"),
    ("core-geosite", "geosite", "geosite.dat", f"{AGENT_LIB_DIR}/geosite.dat", "0644"),
)


_VISION: Final = "xtls-rprx-vision"


def managed_inbounds(profile: ConfigProfilePayload) -> list[ManagedInbound]:
    """The inbounds whose clients are the service's users: every one but gateway bridges."""
    return [
        ManagedInbound(
            tag=inbound.tag,
            protocol="hysteria2" if inbound.protocol == "hysteria2" else "vless",
            flow=_VISION if inbound.protocol == "reality" else "",
        )
        for inbound in profile.inbounds
        if inbound.purpose != "bridge"
    ]


def build_node_spec(
    profile: ConfigProfilePayload,
    reality_keys: RealityKeyBinding | None,
    service_credentials: dict[str, ServiceRouteCredential] | None = None,
) -> NodeSpec:
    """Return the agent resources for one workload; a gateway passes its edges' credentials."""
    inbounds = managed_inbounds(profile)
    users = [users_resource_id(inbound.tag) for inbound in inbounds]
    pins = [pin[0] for pin in _PINS]
    resources: list[dict[str, Any]] = [
        {
            "id": resource_id,
            "kind": "binary_pin",
            "name": name,
            "version": XRAY_VERSION,
            "sources": [f"{XRAY_GITHUB_URL}/v{XRAY_VERSION}/{XRAY_LINUX_ASSET}"],
            "size": XRAY_ARCHIVE_BYTES[XRAY_LINUX_ASSET],
            "sha256": XRAY_SHA256[XRAY_LINUX_ASSET],
            "archive": "zip",
            "member": member,
            "install_path": path,
            "mode": mode,
        }
        for resource_id, name, member, path, mode in _PINS
    ]
    resources.append(
        {
            "id": "core-config",
            "depends_on": pins,
            "kind": "xray_config",
            "path": CORE_CONFIG,
            "config": _with_api(_with_bridge_clients(profile, reality_keys, service_credentials)),
            "api_address": f"127.0.0.1:{CORE_API_PORT}",
        }
    )
    resources.append(
        {
            "id": "core-unit",
            "depends_on": [*pins, "core-config", *users],
            "kind": "systemd_unit",
            "name": CORE_UNIT,
            "content": _core_unit(),
            "enabled": True,
            "state": "started",
            "restart_on": [*pins, "core-config", *users],
        }
    )
    return NodeSpec.model_validate({"resources": resources, "managed_inbounds": inbounds})


def _with_bridge_clients(
    profile: ConfigProfilePayload,
    reality_keys: RealityKeyBinding | None,
    service_credentials: dict[str, ServiceRouteCredential] | None,
) -> dict[str, Any]:
    """Render the profile, giving each gateway bridge its one client (the gateway's edge)."""
    config = render_workload_config(profile, reality_keys=reality_keys, service_credentials=service_credentials)
    bridges = {
        inbound.tag: ref
        for ref, inbound in zip(profile.inbound_refs, profile.inbounds, strict=True)
        if inbound.purpose == "bridge"
    }
    if not bridges:
        return config
    assert reality_keys is not None  # rendering a Reality bridge already required the keys
    inbounds = [
        {
            **inbound,
            "settings": {
                **inbound["settings"],
                "clients": [{"id": bridge_client_uuid(reality_keys, bridges[inbound["tag"]]), "flow": _VISION}],
            },
        }
        if inbound["tag"] in bridges
        else inbound
        for inbound in config["inbounds"]
    ]
    return {**config, "inbounds": inbounds}


def _with_api(config: dict[str, Any]) -> dict[str, Any]:
    """Add Xray's loopback API inbound and per-user traffic counters."""
    routing = dict(config["routing"])
    routing["rules"] = [{"type": "field", "inboundTag": ["api"], "outboundTag": "api"}, *routing["rules"]]
    return {
        **config,
        "api": {"tag": "api", "services": ["HandlerService", "StatsService"]},
        "stats": {},
        "policy": {"levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}}},
        "routing": routing,
        "inbounds": [
            *config["inbounds"],
            {
                "tag": "api",
                "listen": "127.0.0.1",
                "port": CORE_API_PORT,
                "protocol": "dokodemo-door",
                "settings": {"address": "127.0.0.1"},
            },
        ],
    }


def _core_unit() -> str:
    return (
        "[Unit]\n"
        "Description=Network core\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n\n"
        "[Service]\n"
        f"Environment=XRAY_LOCATION_ASSET={AGENT_LIB_DIR}\n"
        f"ExecStart={CORE_BINARY} run -c {CORE_CONFIG}\n"
        "Restart=on-failure\n"
        "RestartSec=3\n"
        "LimitNOFILE=1048576\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
