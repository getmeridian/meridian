"""Setup runtime pieces for Meridian's own control service (`ControlPlaneIntent.backend == "meridian"`).

The control bootstrap installs the service behind the control server's nginx
and saves its URL and admin token where the panel's lived (`cluster.panel`), so
subscription links keep their shape. Verification reads the service and each
user's delivered Xray JSON instead of the Remnawave panel.
"""

from __future__ import annotations

import hashlib
import ipaddress
import secrets
import uuid
from collections.abc import Callable, Mapping

from meridian.cluster import ClusterConfig
from meridian.compiler.models import (
    AgentRuntimePayload,
    ControlServicePayload,
    ControlUserPayload,
    NodeWorkloadPayload,
    ResourcePlan,
    ServerBaselinePayload,
    canonical_hash,
)
from meridian.control.client import ControlClient
from meridian.control.command_models import NodeCommandRecord
from meridian.control.models import ServiceConfig
from meridian.control_artifacts import agent_binary, require_agent_build, service_package
from meridian.core.cancellation import CancellationToken
from meridian.core.defaults import DEFAULT_SNI
from meridian.core.errors import ProvisioningError
from meridian.core.output import OperationContext
from meridian.core.reporters import NoopReporter, Reporter
from meridian.host_runtime import CONTROL_SERVICE_STATE_DIR
from meridian.provision.ensure import ensure_file_content
from meridian.reconciler.control_service_driver import xray_subscription_is_valid
from meridian.reconciler.meridian_drivers import MeridianDriverContext, build_meridian_drivers
from meridian.reconciler.resources import ResourceAction, ResourceDrivers, ResourceReconcileError
from meridian.reconciler.server_evidence import file_content_or_absent
from meridian.reconciler.workloads import LocalRealityKeyFactory, WorkloadStateManager
from meridian.servers import ServerEntry
from meridian.ssh import ServerConnection

_ATTESTATION_PATH = f"{CONTROL_SERVICE_STATE_DIR}.attestation"
PersistCluster = Callable[[ClusterConfig], None]


def control_payload(plan: ResourcePlan) -> ControlServicePayload:
    controls = [resource.payload for resource in plan.resources if isinstance(resource.payload, ControlServicePayload)]
    if len(controls) != 1:
        raise ResourceReconcileError("A reviewed topology must contain exactly one control service.")
    return controls[0]


def plan_harden(plan: ResourcePlan, server_ref: str) -> bool:
    """Whether the reviewed baseline hardens this server (SSH key-only, fail2ban)."""
    return next(
        (
            resource.payload.harden
            for resource in plan.resources
            if isinstance(resource.payload, ServerBaselinePayload) and resource.payload.server_ref == server_ref
        ),
        True,
    )


def bootstrap_reality(plan: ResourcePlan) -> tuple[int, str]:
    """The Reality port and SNI nginx's interim 443 fallback uses before the reviewed stream replaces it.

    Only the exit running on the control server shares its nginx; another exit's
    port would be wrong here, and adding one that sorts first re-ran the bootstrap.
    """
    control_ref = control_payload(plan).server_ref
    for resource in plan.resources:
        payload = resource.payload
        if not isinstance(payload, NodeWorkloadPayload) or payload.server_ref != control_ref:
            continue
        if payload.profile.workload_kind != "exit":
            continue
        for inbound in payload.profile.inbounds:
            if inbound.protocol == "reality":
                # A self-steal name would point the fallback at this very server.
                return inbound.listen_port, DEFAULT_SNI if inbound.reality_dest_port else inbound.reality_sni
        break
    return 10443, DEFAULT_SNI


def control_client(cluster: ClusterConfig) -> ControlClient:
    if not cluster.panel.url or not cluster.panel.api_token:
        raise ResourceReconcileError("Control-service credentials are unavailable before bootstrap.")
    return ControlClient(cluster.panel.url, cluster.panel.api_token)


def meridian_drivers(
    plan: ResourcePlan,
    cluster: ClusterConfig,
    *,
    persist: PersistCluster,
    connections: Mapping[str, ServerConnection],
    addresses: Mapping[str, str],
    user_cut_off: Mapping[str, list[NodeCommandRecord]] | None = None,
) -> ResourceDrivers:
    return build_meridian_drivers(
        MeridianDriverContext(
            plan=plan,
            cluster=cluster,
            client=lambda: control_client(cluster),
            workloads=WorkloadStateManager(cluster, persist=persist, key_factory=LocalRealityKeyFactory()),
            connection_for=connections.__getitem__,
            server_addresses=addresses,
            agent_binary=agent_binary,
            user_cut_off=user_cut_off if user_cut_off is not None else {},
        )
    )


def bootstrap_control_service(
    payload: ControlServicePayload,
    plan: ResourcePlan,
    cluster: ClusterConfig,
    entry: ServerEntry,
    connection: ServerConnection,
    *,
    harden: bool,
    reality_port: int,
    reality_sni: str,
    cancellation: CancellationToken | None = None,
    reporter: Reporter | None = None,
    operation: OperationContext | None = None,
) -> None:
    """Install nginx, the IP (or control-domain) certificate, and the control service; save how to reach it."""
    from meridian.provision import ProvisionContext, Provisioner, build_setup_steps
    from meridian.provision.control_service import ControlServiceInstall

    if cluster.panel.server_ip and cluster.panel.server_ip != entry.host and cluster.panel.api_token:
        raise ResourceReconcileError(
            "The selected control server conflicts with the saved control service.",
            hint="Go back and select the existing control server.",
            category="user",
        )
    if any(isinstance(resource.payload, AgentRuntimePayload) for resource in plan.resources):
        # Found only once the first agent installed, a missing build left a service with no nodes.
        require_agent_build()
    cluster.fleet_id = cluster.fleet_id or str(uuid.uuid4())
    cluster.panel.server_ip = entry.host
    cluster.panel.ssh_user = entry.user
    cluster.panel.ssh_port = entry.port
    cluster.panel.secret_path = cluster.panel.secret_path or secrets.token_hex(12)
    cluster.panel.sub_path = cluster.panel.sub_path or secrets.token_hex(8)
    cluster.panel.api_token = cluster.panel.api_token or secrets.token_urlsafe(32)
    # Like the panel, the service answers on its domain once it has one; agents and links follow it.
    cluster.panel.url = _service_url(entry, payload, cluster.panel.secret_path)
    cluster.save()

    ctx = ProvisionContext(
        ip=entry.host,
        user=entry.user,
        domain=payload.public_hostname,
        sni=reality_sni,
        xhttp_enabled=False,
        hosted_page=True,
        harden=harden,
    )
    ctx.cluster = cluster
    ctx.reality_port = reality_port
    ctx.web_base_path = cluster.panel.secret_path
    ctx.info_page_path = cluster.panel.sub_path
    ctx.control_service = ControlServiceInstall(
        package=service_package(),
        config=ServiceConfig(
            fleet_id=cluster.fleet_id,
            admin_token_sha256=hashlib.sha256(cluster.panel.api_token.encode("utf-8")).hexdigest(),
            listen_port=payload.service_port,
            # The web console's session cookie is scoped to the path nginx strips.
            public_path=f"/{cluster.panel.secret_path}",
        ),
    )
    results = Provisioner(build_setup_steps(ctx)).run(
        connection,
        ctx,
        reporter=reporter or NoopReporter(),
        operation=operation,
        cancellation=cancellation,
    )
    failed = [result for result in results if result.status == "failed"]
    if failed:
        raise ProvisioningError(
            "Setup failed",
            hint=f"Step '{failed[0].name}' failed: {failed[0].detail}\nRun: meridian check {entry.host}",
        )
    written = ensure_file_content(
        connection, _ATTESTATION_PATH, f"{service_attestation(payload, plan)}\n", mode="600", create_parent=True
    )
    if not written.ok:
        raise ResourceReconcileError(f"Could not attest the control service: {written.detail}")


def bootstrap_reviewed_control_service(
    payload: ControlServicePayload,
    plan: ResourcePlan,
    cluster: ClusterConfig,
    entry: ServerEntry,
    connection: ServerConnection,
    *,
    cancellation: CancellationToken | None = None,
    reporter: Reporter | None = None,
    operation: OperationContext | None = None,
) -> None:
    """`bootstrap_control_service()` with the values a reviewed plan gives it (apply and restore)."""
    reality_port, reality_sni = bootstrap_reality(plan)
    bootstrap_control_service(
        payload,
        plan,
        cluster,
        entry,
        connection,
        harden=plan_harden(plan, payload.server_ref),
        reality_port=reality_port,
        reality_sni=reality_sni,
        cancellation=cancellation,
        reporter=reporter,
        operation=operation,
    )


def control_service_ready(
    action: ResourceAction,
    plan: ResourcePlan,
    cluster: ClusterConfig,
    entry: ServerEntry,
    connection: ServerConnection,
) -> bool:
    payload = action.resource.payload
    if not isinstance(payload, ControlServicePayload):
        raise ResourceReconcileError(f"Control service observer received {payload.kind}.")
    if not cluster.panel.api_token or cluster.panel.server_ip != entry.host:
        return False
    if cluster.panel.url != _service_url(entry, payload, cluster.panel.secret_path):
        # A saved URL on the old name would hand agents and links a certificate that no longer matches.
        return False
    # A lost connection is unavailable evidence, never a reason to reinstall the service.
    read = connection.get_text(_ATTESTATION_PATH, timeout=15)
    attestation = file_content_or_absent(action.resource.logical_id, read, "control-service attestation")
    if attestation is None or attestation.strip() != service_attestation(payload, plan):
        return False
    with control_client(cluster) as client:
        return client.ping()


def verify_control_service(plan: ResourcePlan, cluster: ClusterConfig) -> tuple[str, int, dict[str, str]]:
    """Return (service version, nodes applied, subscription URL per user) or raise."""
    with control_client(cluster) as client:
        version = client.health().version
        nodes = [item.payload for item in plan.resources if isinstance(item.payload, NodeWorkloadPayload)]
        for node in nodes:
            status = client.node(node.workload_ref)
            if status is None or not status.in_sync:
                raise ResourceReconcileError(f"Node {node.workload_ref} has not applied its current document.")
        urls: dict[str, str] = {}
        for item in plan.resources:
            if not isinstance(item.payload, ControlUserPayload):
                continue
            user = client.get_user(item.payload.username)
            if user is None:
                raise ResourceReconcileError(f"Managed access user {item.payload.username!r} is missing.")
            document = client.fetch_subscription(user.sub_token, document="json")
            if not xray_subscription_is_valid(_Document(urls.get(item.payload.username, "sub"), document)):
                raise ResourceReconcileError(f"Canonical subscription for {item.payload.username!r} is invalid.")
            urls[item.payload.username] = cluster.panel.subscription_url(user.sub_token)
    return version, len(nodes), urls


class _Document:
    """The shape `xray_subscription_is_valid` reads from a panel subscription."""

    def __init__(self, url: str, content: str) -> None:
        self.url = url
        self.content = content


def service_base_url(ip: str, domain: str, secret_path: str) -> str:
    """Where nginx serves the control service: its domain (or bracketed IP) and secret path."""
    host = domain or ip
    try:
        if ipaddress.ip_address(host).version == 6:
            host = f"[{host}]"
    except ValueError:
        pass
    return f"https://{host}/{secret_path}/"


def _service_url(entry: ServerEntry, payload: ControlServicePayload, secret_path: str) -> str:
    return service_base_url(entry.host, payload.public_hostname, secret_path).rstrip("/")


def service_attestation(payload: ControlServicePayload, plan: ResourcePlan) -> str:
    """Hash what the bootstrap consumes, so unrelated plan changes never reinstall the service."""
    contract = plan.deployment_contract
    target = contract.server_targets.get(payload.server_ref)
    return canonical_hash(
        {
            "payload": payload.model_dump(mode="json"),
            "control_target": target.model_dump(mode="json") if target is not None else None,
            "package": service_package().identity,
            "renderer_contract": contract.renderer_contract,
        }
    )
