"""Build the topology intent behind `meridian deploy IP`: one server, one exit.

The control plane stays on the server's IP certificate; a domain, when given,
carries the exit's TLS paths (XHTTP, WSS, Hysteria2). One server cannot route
the same SNI to both the control plane and an exit.
"""

from __future__ import annotations

from meridian.compiler import compile_topology
from meridian.compiler.models import NodeWorkloadPayload
from meridian.core.deploy import DeployPlan, DeployPorts, DeployRequest
from meridian.core.errors import LocalStateError, RemnawaveFleetError
from meridian.core.topology import (
    AccessIntent,
    ControlPlaneIntent,
    ExitIntent,
    SetupIntent,
    TlsFingerprint,
)
from meridian.setup.default_paths import default_exit_paths

DEFAULT_EXIT_ID = "main"
DEFAULT_CLIENT = "default"
# Stands in for a server that has no saved profile yet (dry runs only).
PENDING_SERVER_REF = "srv-pending"


def single_server_intent(
    existing: SetupIntent | None,
    *,
    server_ref: str,
    sni: str = "",
    domain: str = "",
    self_steal: bool = False,
    client_name: str = "",
    warp: bool = False,
    geo_block: bool = True,
    hysteria2: bool = True,
    fingerprint: TlsFingerprint | None = None,
    address: str = "",
    region: str = "",
) -> SetupIntent:
    """Return the intent for deploying (or redeploying) one server.

    A fresh fleet gets a control plane plus one exit on ``server_ref``. An
    existing fleet may only be redeployed on a server that already runs an
    exit; its other exits, relays, gateways, and users are preserved.
    """
    if existing is None:
        exit_ = ExitIntent(
            id=DEFAULT_EXIT_ID,
            server_ref=server_ref,
            region=region,
            paths=default_exit_paths(
                DEFAULT_EXIT_ID,
                address=address,
                reality_sni=sni,
                domain=domain,
                self_steal=self_steal,
                fingerprint=fingerprint,
                hysteria2=hysteria2,
            ),
            warp=warp,
            geo_block=geo_block,
        )
        return SetupIntent(
            control=ControlPlaneIntent(server_ref=server_ref),
            exits=[exit_],
            default_egress_ref=exit_.id,
            access=AccessIntent(users=[client_name or DEFAULT_CLIENT]),
        )

    if existing.control.backend == "remnawave":
        raise RemnawaveFleetError()
    current = next((item for item in existing.exits if item.server_ref == server_ref), None)
    if current is None:
        raise LocalStateError(
            "This server is not an exit in your saved fleet.",
            hint="Add it with `meridian node add`, or change the fleet with `meridian setup`.",
            category="user",
        )
    updated = current.model_copy(
        update={
            "paths": default_exit_paths(
                current.id,
                address=address,
                reality_sni=sni,
                domain=domain,
                self_steal=self_steal,
                fingerprint=fingerprint,
                hysteria2=hysteria2,
                previous=current.paths,
            ),
            "warp": warp,
            "geo_block": geo_block,
            "region": region or current.region,
        }
    )
    users = list(existing.access.users)
    if client_name and client_name not in users:
        users.append(client_name)
    return existing.model_copy(
        update={
            "exits": [updated if item.id == current.id else item for item in existing.exits],
            "access": existing.access.model_copy(update={"users": users}),
        }
    )


def request_intent(
    existing: SetupIntent | None,
    request: DeployRequest,
    *,
    server_ref: str,
    address: str = "",
) -> SetupIntent:
    """Return the reviewed intent one deploy request describes."""
    return single_server_intent(
        existing,
        server_ref=server_ref,
        address=address,
        sni=request.reality_sni,
        domain=request.domain,
        self_steal=request.self_steal,
        client_name=request.client_name,
        warp=request.warp,
        geo_block=request.geo_block,
        hysteria2=request.hysteria2,
        fingerprint=request.fingerprint,
        region=request.country,
    ).model_copy(update={"harden": request.harden, "forward_client_ips": request.client_ips})


def preview_single_server_deploy(
    existing: SetupIntent | None,
    request: DeployRequest,
    *,
    server_ref: str,
    server_ip: str,
    panel_secret_path: str = "",
    panel_sub_path: str = "",
) -> DeployPlan:
    """Compile the intent a deploy would apply, without SSH or local writes.

    Raises :class:`LocalStateError` when the request cannot target this fleet.
    """
    intent = request_intent(existing, request, server_ref=server_ref, address=server_ip)
    plan = compile_topology(intent)
    ports = {
        inbound.protocol: inbound.listen_port
        for resource in plan.resources
        if isinstance(resource.payload, NodeWorkloadPayload)
        for inbound in resource.payload.profile.inbounds
    }
    paths = {path.protocol: path.path for item in intent.exits if item.server_ref == server_ref for path in item.paths}
    return DeployPlan(
        mode="first_deploy" if existing is None else "redeploy",
        server_ip=server_ip,
        ports=DeployPorts(
            reality_port=ports.get("reality", 0),
            xhttp_port=ports.get("xhttp", 0),
            wss_port=ports.get("wss", 0),
        ),
        secret_path=panel_secret_path,
        xhttp_path=paths.get("xhttp", ""),
        ws_path=paths.get("wss", ""),
        info_page_path=panel_sub_path,
        node_count=len(intent.exits),
        relay_count=len(intent.transparent_relays),
    )
