"""Pure deploy planning for meridian-core services."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable

from meridian.core.deploy import DeployMode
from meridian.core.models import CoreModel


class DeployPlanningError(ValueError):
    """Raised when a deploy request cannot be planned safely."""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


class DeployNodeState(CoreModel):
    """Existing node state relevant to redeploy planning."""

    ip: str
    xhttp_path: str = ""
    ws_path: str = ""


class DeployClusterState(CoreModel):
    """Cluster state projection required by deploy planning."""

    is_configured: bool
    panel_secret_path: str = ""
    panel_sub_path: str = ""
    existing_node: DeployNodeState | None = None
    node_count: int = 0
    relay_count: int = 0


class DeployPorts(CoreModel):
    """Deterministic per-node port layout."""

    xhttp_port: int
    reality_port: int
    wss_port: int


class DeployPlan(CoreModel):
    """Core-owned deploy plan consumed by provisioning and panel adapters."""

    mode: DeployMode
    server_ip: str
    ports: DeployPorts
    secret_path: str
    xhttp_path: str
    ws_path: str
    info_page_path: str
    node_count: int
    relay_count: int


def _ip_hash(ip: str) -> int:
    """Deterministic 32-bit hash of an IP string for port arithmetic."""
    return int(hashlib.sha256(ip.encode()).hexdigest()[:8], 16)


def compute_deploy_ports(server_ip: str) -> DeployPorts:
    """Return Meridian's deterministic port layout for a server IP."""
    h = _ip_hash(server_ip)
    return DeployPorts(
        xhttp_port=30000 + (h % 10000),
        reality_port=10000 + h % 1000,
        wss_port=20000 + (h % 10000),
    )


def compute_relay_port(relay_ip: str) -> int:
    """Return the deterministic Xray port for a relay inbound (range 40000-49999)."""
    return 40000 + (_ip_hash(relay_ip) % 10000)


def build_deploy_plan(
    server_ip: str,
    state: DeployClusterState,
    *,
    token_hex: Callable[[int], str] = secrets.token_hex,
) -> DeployPlan:
    """Build a deploy/redeploy plan without performing I/O."""
    if not state.is_configured:
        mode: DeployMode = "first_deploy"
    elif state.existing_node is not None:
        mode = "redeploy"
    else:
        raise DeployPlanningError(
            f"Cluster already configured -- use 'meridian node add {server_ip}' to add a new node",
            hint="'meridian deploy' is for initial deployment or redeploying existing nodes.\n"
            "To add new nodes to an existing cluster: meridian node add IP",
        )

    existing_node = state.existing_node
    return DeployPlan(
        mode=mode,
        server_ip=server_ip,
        ports=compute_deploy_ports(server_ip),
        secret_path=state.panel_secret_path if mode == "redeploy" and state.panel_secret_path else token_hex(12),
        xhttp_path=existing_node.xhttp_path if existing_node and existing_node.xhttp_path else token_hex(8),
        ws_path=existing_node.ws_path if existing_node and existing_node.ws_path else token_hex(8),
        info_page_path=state.panel_sub_path or token_hex(8),
        node_count=state.node_count,
        relay_count=state.relay_count,
    )
