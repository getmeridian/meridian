"""Deploy planning use cases for the local Meridian Engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from meridian.cluster import ClusterConfig
from meridian.config import HOME_DISPLAY
from meridian.core.deploy import DeployPlan, DeployRequest
from meridian.core.deploy_validation import (
    DeployValidationError,
    normalize_deploy_request,
    validate_deploy_target,
)
from meridian.core.errors import EngineError, MeridianError
from meridian.core.inputs import is_ip_deploy_target, is_local_deploy_target
from meridian.core.servers import ServerProfile
from meridian.servers import ServerEntry
from meridian.setup.single_server import PENDING_SERVER_REF, preview_single_server_deploy


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


def _user_error(exc: DeployValidationError) -> EngineError:
    return EngineError(str(exc), hint=exc.hint, category="user")


def resolve_deploy_target(request: DeployRequest, registry: ServerLookup) -> ResolvedDeployTarget:
    """Resolve a deploy request to a concrete IP/local target without opening SSH."""
    try:
        request = normalize_deploy_request(request)
        server_ip = request.ip
        entry = None
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
        elif server_ip and not is_local_deploy_target(server_ip):
            entry = registry.find(server_ip)

        validate_deploy_target(server_ip)
    except DeployValidationError as exc:
        raise _user_error(exc) from exc

    # An empty user or zero port means "the saved value, else the SSH default".
    return ResolvedDeployTarget(
        server_ip=server_ip,
        ssh_user=request.user or (entry.ssh_user if entry else "") or "root",
        ssh_port=request.ssh_port or (entry.ssh_port if entry else 0) or 22,
    )


def dry_run_deploy_request(
    request: DeployRequest,
    *,
    cluster: ClusterConfig,
    registry: ServerLookup,
) -> DeployPlan:
    """Compile the single-server topology a deploy would apply, without SSH or writes."""
    target = resolve_deploy_target(request, registry)
    if cluster.is_configured and cluster.topology_intent is None:
        raise EngineError(
            "This cluster.yml was written by a Meridian 4.0 development build",
            hint=f"Move {HOME_DISPLAY}/cluster.yml aside, then deploy again to rebuild it from scratch.",
            category="user",
        )
    entry = registry.find(target.server_ip)
    try:
        return preview_single_server_deploy(
            cluster.topology_intent,
            normalize_deploy_request(request),
            server_ref=entry.id if entry is not None and entry.id else PENDING_SERVER_REF,
            server_ip=target.server_ip,
            panel_secret_path=cluster.panel.secret_path,
            panel_sub_path=cluster.panel.sub_path,
        )
    except MeridianError as exc:
        raise EngineError(str(exc), hint=exc.hint, category=exc.category) from exc
