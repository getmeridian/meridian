"""Deploy planning use cases for the local Meridian Engine."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from meridian.cluster import ClusterConfig
from meridian.core.deploy import DeployRequest
from meridian.core.deploy_planning import (
    DeployClusterState,
    DeployNodeState,
    DeployPlan,
    DeployPlanningError,
    build_deploy_plan,
)
from meridian.core.deploy_validation import (
    DeployValidationError,
    normalize_deploy_request,
    validate_deploy_target,
)
from meridian.core.inputs import is_ip_deploy_target, is_local_deploy_target
from meridian.core.models import ErrorCategory
from meridian.core.servers import ServerProfile
from meridian.servers import ServerEntry


class EngineError(RuntimeError):
    """Typed Engine error for CLI/API adapters to render."""

    def __init__(self, message: str, *, hint: str = "", category: ErrorCategory = "user") -> None:
        super().__init__(message)
        self.hint = hint
        self.category = category


@dataclass(frozen=True)
class ResolvedDeployTarget:
    """Deploy target after no-I/O request and registry resolution."""

    server_ip: str
    ssh_user: str
    ssh_port: int = 22


class ServerLookup(Protocol):
    """Minimal registry surface needed for deploy planning."""

    def find(self, query: str) -> ServerEntry | ServerProfile | None:
        """Return a registered server by host/name, if known."""


def _user_error(exc: DeployPlanningError | DeployValidationError) -> EngineError:
    return EngineError(str(exc), hint=exc.hint, category="user")


def resolve_deploy_target(request: DeployRequest, registry: ServerLookup) -> ResolvedDeployTarget:
    """Resolve a deploy request to a concrete IP/local target without opening SSH."""
    try:
        request = normalize_deploy_request(request)
        server_ip = request.ip
        ssh_user = request.user
        ssh_port = request.ssh_port
        if request.requested_server:
            if is_local_deploy_target(request.requested_server):
                server_ip = request.requested_server
            else:
                entry = registry.find(request.requested_server)
                if not entry:
                    if is_ip_deploy_target(request.requested_server):
                        server_ip = request.requested_server
                    else:
                        raise DeployValidationError(
                            f"Server '{request.requested_server}' not found",
                            hint="See registered servers: meridian server list",
                        )
                else:
                    server_ip = entry.host
                    entry_user = _entry_user(entry)
                    if request.user == "root" and entry_user:
                        ssh_user = entry_user
                    if request.ssh_port == 22:
                        ssh_port = _entry_port(entry)

        validate_deploy_target(server_ip)
    except DeployValidationError as exc:
        raise _user_error(exc) from exc

    return ResolvedDeployTarget(server_ip=server_ip, ssh_user=ssh_user, ssh_port=ssh_port)


def _entry_user(entry: ServerEntry | ServerProfile) -> str:
    return getattr(entry, "user", getattr(entry, "ssh_user", "root"))


def _entry_port(entry: ServerEntry | ServerProfile) -> int:
    return getattr(entry, "port", getattr(entry, "ssh_port", 22))


def project_deploy_cluster_state(cluster: ClusterConfig, server_ip: str) -> DeployClusterState:
    """Project persistent cluster config into the pure core deploy planner state."""
    existing_node = cluster.find_node(server_ip)
    return DeployClusterState(
        is_configured=cluster.is_configured,
        panel_secret_path=cluster.panel.secret_path,
        panel_sub_path=cluster.panel.sub_path,
        existing_node=(
            DeployNodeState(
                ip=existing_node.ip,
                xhttp_path=existing_node.xhttp_path,
                ws_path=existing_node.ws_path,
            )
            if existing_node is not None
            else None
        ),
        node_count=len(cluster.nodes),
        relay_count=len(cluster.relays),
    )


def plan_deploy_request(
    request: DeployRequest,
    *,
    cluster: ClusterConfig,
    registry: ServerLookup,
    token_hex: Callable[[int], str] = secrets.token_hex,
) -> DeployPlan:
    """Validate, resolve, and plan a deploy request without SSH or panel I/O."""
    target = resolve_deploy_target(request, registry)
    try:
        return build_deploy_plan(
            target.server_ip,
            project_deploy_cluster_state(cluster, target.server_ip),
            token_hex=token_hex,
        )
    except DeployPlanningError as exc:
        raise _user_error(exc) from exc


def dry_run_deploy_request(
    request: DeployRequest,
    *,
    cluster: ClusterConfig,
    registry: ServerLookup,
) -> DeployPlan:
    """Build a deterministic deploy plan for process/API dry-run output."""
    return plan_deploy_request(request, cluster=cluster, registry=registry, token_hex=lambda n: "0" * (n * 2))
