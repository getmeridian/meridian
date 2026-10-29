"""The control plane's bootstrap and readiness, shared by setup runtime and its tests.

The Remnawave bootstrap runs the provisioner once, then attests only its own
inputs on the server, so edits elsewhere in the fleet never re-run it.
"""

from __future__ import annotations

import secrets
from urllib.parse import urlparse

from meridian import __version__
from meridian.cluster import ClusterConfig
from meridian.compiler.models import (
    ConfigProfilePayload,
    ControlPlaneRuntimePayload,
    ControlServicePayload,
    NodeRuntimePayload,
    NodeWorkloadPayload,
    ResourcePlan,
    ServerBaselinePayload,
    canonical_hash,
)
from meridian.config import REMNAWAVE_BACKEND_IMAGE, REMNAWAVE_SUBSCRIPTION_PAGE_IMAGE
from meridian.core.cancellation import CancellationToken
from meridian.core.defaults import DEFAULT_SNI
from meridian.core.errors import LocalStateError
from meridian.core.output import OperationContext
from meridian.core.reporters import NoopReporter, Reporter
from meridian.panel_bootstrap import ensure_control_plane_access, run_provisioner
from meridian.provision.ensure import ensure_file_content
from meridian.provision.runtime_migration import LEGACY_PANEL_DIR_ABSENT
from meridian.reconciler.resources import ResourceAction, ResourceReconcileError, UnknownResourceOutcome
from meridian.reconciler.server_evidence import (
    file_content_or_absent,
    negated_path_is_absent,
    require_successful_evidence,
)
from meridian.remnawave import MeridianPanel
from meridian.resolve import ResolvedServer
from meridian.servers import ServerEntry
from meridian.ssh import ServerConnection

_CONTROL_ATTESTATION_PATH = "/var/lib/meridian/control-runtime.attestation"


def control_runtime_ready(
    action: ResourceAction,
    plan: ResourcePlan,
    cluster: ClusterConfig,
    entry: ServerEntry,
    connection: ServerConnection,
    panel: MeridianPanel,
) -> bool:
    payload = action.resource.payload
    if not isinstance(payload, ControlPlaneRuntimePayload):
        raise ResourceReconcileError(f"Control runtime observer received {payload.kind}.")
    expected_hostname = payload.public_hostname.rstrip(".").casefold()
    actual_hostname = (urlparse(cluster.panel.url).hostname or "").rstrip(".").casefold()
    identity_matches = (
        bool(cluster.panel.api_token)
        and cluster.panel.server_ip == entry.host
        and cluster.panel.ssh_user == entry.user
        and cluster.panel.ssh_port == entry.port
        and (not expected_hostname or actual_hostname == expected_hostname)
    )
    if not identity_matches:
        return False
    # A surviving pre-rename panel directory is drift: bootstrap moves it.
    legacy = connection.run(LEGACY_PANEL_DIR_ABSENT, timeout=15)
    if not negated_path_is_absent(action.resource.logical_id, legacy, "legacy panel directory"):
        return False
    attestation = connection.get_text(_CONTROL_ATTESTATION_PATH, timeout=15)
    expected_attestation = control_runtime_attestation(action, plan)
    attestation_content = file_content_or_absent(
        action.resource.logical_id,
        attestation,
        "control-plane attestation",
    )
    if attestation_content != f"{expected_attestation}\n":
        return False
    sockets = connection.run("ss -H -lnt 2>/dev/null", timeout=15)
    require_successful_evidence(action.resource.logical_id, sockets, "control-plane listener inspection")
    if not _socket_table_has_port(sockets.stdout, payload.internal_https_port):
        return False
    if not panel.ping():
        raise ResourceReconcileError(
            "Control-plane API readiness evidence is unavailable.",
            hint="Restore panel connectivity, then inspect the topology again.",
        )
    return True


def write_control_runtime_attestation(
    action: ResourceAction,
    plan: ResourcePlan,
    connection: ServerConnection,
) -> None:
    result = ensure_file_content(
        connection,
        _CONTROL_ATTESTATION_PATH,
        f"{control_runtime_attestation(action, plan)}\n",
        mode="600",
        create_parent=True,
    )
    if result.ok:
        return
    if result.result is not None and result.result.returncode == 124:
        raise UnknownResourceOutcome(
            f"Timed out while attesting {action.resource.logical_id}; observation is required."
        )
    raise ResourceReconcileError(f"Could not attest {action.resource.logical_id}: {result.detail}")


def control_runtime_attestation(action: ResourceAction, plan: ResourcePlan) -> str:
    """Hash only what the bootstrap consumes, so other servers joining or leaving never re-run it."""
    server_ref = control_payload(plan).server_ref
    contract = plan.deployment_contract
    target = contract.server_targets.get(server_ref)
    ports, reality_sni = bootstrap_profile_values(plan)
    return canonical_hash(
        {
            "resource": action.expected_hash,
            "control_target": target.model_dump(mode="json") if target is not None else None,
            "runtime_pins": contract.runtime_pins,
            "renderer_contract": contract.renderer_contract,
            "bootstrap_profile": {"ports": ports, "reality_sni": reality_sni},
            "harden": plan_harden(plan, server_ref),
        }
    )


def _socket_table_has_port(output: str, port: int) -> bool:
    suffix = f":{port}"
    return any(field.endswith(suffix) for line in output.splitlines() for field in line.split())


def control_payload(
    plan: ResourcePlan,
) -> ControlPlaneRuntimePayload | ControlServicePayload:
    controls = [
        resource.payload
        for resource in plan.resources
        if isinstance(resource.payload, ControlPlaneRuntimePayload | ControlServicePayload)
    ]
    if len(controls) != 1:
        raise ResourceReconcileError("A reviewed topology must contain exactly one control plane.")
    return controls[0]


def bootstrap_control_plane(
    payload: ControlPlaneRuntimePayload,
    plan: ResourcePlan,
    cluster: ClusterConfig,
    entry: ServerEntry,
    connection: ServerConnection,
    *,
    cancellation: CancellationToken | None = None,
    reporter: Reporter | None = None,
    operation: OperationContext | None = None,
) -> None:
    if cluster.panel.server_ip and cluster.panel.server_ip != entry.host and cluster.panel.api_token:
        raise LocalStateError(
            "The selected control server conflicts with the saved panel.",
            hint="Go back and select the existing control server.",
        )

    secret_path = cluster.panel.secret_path or secrets.token_hex(12)
    info_page_path = cluster.panel.sub_path or secrets.token_hex(8)
    cluster.panel.server_ip = entry.host
    cluster.panel.ssh_user = entry.user
    cluster.panel.ssh_port = entry.port
    cluster.panel.secret_path = secret_path
    cluster.panel.sub_path = info_page_path
    cluster.save()

    ports, reality_sni = bootstrap_profile_values(plan)
    resolved = ResolvedServer(
        ip=entry.host,
        user=entry.user,
        local_mode=connection.local_mode,
        conn=connection,
    )
    run_provisioner(
        resolved=resolved,
        cluster=cluster,
        domain=payload.public_hostname,
        sni=reality_sni,
        harden=plan_harden(plan, payload.server_ref),
        secret_path=secret_path,
        xhttp_port=ports["xhttp"],
        reality_port=ports["reality"],
        wss_port=ports["wss"],
        info_page_path=info_page_path,
        backend_image=plan.deployment_contract.runtime_pins.get("remnawave_backend_image", REMNAWAVE_BACKEND_IMAGE),
        subscription_page_image=plan.deployment_contract.runtime_pins.get(
            "remnawave_subscription_page_image", REMNAWAVE_SUBSCRIPTION_PAGE_IMAGE
        ),
        reporter=reporter or NoopReporter(),
        operation=operation,
        cancellation=cancellation,
    )
    ensure_control_plane_access(
        resolved=resolved,
        cluster=cluster,
        domain=payload.public_hostname,
        secret_path=secret_path,
        info_page_path=info_page_path,
        version=__version__,
    )


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


def bootstrap_profile_values(
    plan: ResourcePlan,
) -> tuple[dict[str, int], str]:
    ports = {
        "reality": 10443,
        "xhttp": 30443,
        "wss": 20443,
    }
    reality_sni = DEFAULT_SNI
    # Only the exit running on the control server shares its nginx; another exit's
    # ports would be wrong here, and adding one that sorts first re-ran the bootstrap.
    control_ref = control_payload(plan).server_ref
    local_workloads = {
        resource.payload.workload_ref
        for resource in plan.resources
        if isinstance(resource.payload, NodeRuntimePayload | NodeWorkloadPayload)
        and resource.payload.server_ref == control_ref
    }
    profiles = [
        resource.payload.profile if isinstance(resource.payload, NodeWorkloadPayload) else resource.payload
        for resource in plan.resources
        if isinstance(resource.payload, ConfigProfilePayload | NodeWorkloadPayload)
    ]
    for payload in profiles:
        if payload.workload_kind != "exit":
            continue
        if payload.workload_id not in local_workloads:
            continue
        for inbound in payload.inbounds:
            if inbound.protocol in ports:
                ports[inbound.protocol] = inbound.listen_port
            # A self-steal name would point the bootstrap's interim 443 fallback at this very
            # server; the reviewed stream replaces it with the local fallback anyway.
            if inbound.protocol == "reality" and not inbound.reality_dest_port:
                reality_sni = inbound.reality_sni
        break
    return ports, reality_sni
