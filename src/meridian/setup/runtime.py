"""Runtime bridge from reviewed setup intent to checkpointed convergence."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from meridian import __version__
from meridian.cluster import ClusterConfig
from meridian.compiler import compile_topology
from meridian.compiler.models import (
    AccessUserPayload,
    ConfigProfilePayload,
    ControlPlaneRuntimePayload,
    NodeRuntimePayload,
    ResourcePlan,
)
from meridian.core.errors import LocalStateError
from meridian.core.setup import SetupDraft
from meridian.core.topology import SetupIntent
from meridian.panel_bootstrap import (
    ensure_control_plane_access,
    run_provisioner,
)
from meridian.reconciler.contract_drivers import ContractResourceDriver
from meridian.reconciler.remnawave_drivers import (
    RemnawaveDriverContext,
    build_remnawave_drivers,
)
from meridian.reconciler.resource_executor import (
    execute_resource_plan,
    inspect_resource_plan,
)
from meridian.reconciler.resources import (
    ResourceDrivers,
    ResourceExecutionResult,
    ResourceInspectionResult,
    ResourceReconcileError,
)
from meridian.reconciler.server_drivers import (
    ServerDriverContext,
    build_server_drivers,
)
from meridian.reconciler.workloads import (
    SSHRealityKeyFactory,
    WorkloadStateManager,
)
from meridian.remnawave import MeridianPanel
from meridian.resolve import ResolvedServer
from meridian.servers import ServerEntry, ServerRegistry
from meridian.ssh import ServerConnection

PersistCluster = Callable[[ClusterConfig], None]
PanelFactory = Callable[[str, str], MeridianPanel]
ConnectionBuilder = Callable[[ServerEntry], ServerConnection]
ControlBootstrap = Callable[
    [ControlPlaneRuntimePayload, ResourcePlan, ClusterConfig, ServerEntry, ServerConnection],
    None,
]


@dataclass(frozen=True)
class SetupReview:
    """Deterministic review boundary shown before any remote mutation."""

    intent: SetupIntent
    plan: ResourcePlan


@dataclass(frozen=True)
class SetupVerification:
    """Canonical post-apply evidence used by handoff presentation."""

    plan_hash: str
    node_count: int
    subscription_urls: Mapping[str, str]


class SetupRuntime:
    """Compile, converge, and verify one resumable setup draft."""

    def __init__(
        self,
        registry: ServerRegistry,
        *,
        cluster_loader: Callable[[], ClusterConfig] = ClusterConfig.load,
        persist: PersistCluster | None = None,
        panel_factory: PanelFactory = MeridianPanel,
        connection_builder: ConnectionBuilder | None = None,
        control_bootstrap: ControlBootstrap | None = None,
    ) -> None:
        self._registry = registry
        self._cluster_loader = cluster_loader
        self._persist = persist or (lambda cluster: cluster.save())
        self._panel_factory = panel_factory
        self._connection_builder = connection_builder or _connection_for_entry
        self._control_bootstrap = control_bootstrap or _bootstrap_control_plane

    def review(self, draft: SetupDraft) -> SetupReview:
        intent = _draft_intent(draft)
        return SetupReview(intent=intent, plan=compile_topology(intent))

    def apply_intent(
        self,
        intent: SetupIntent,
    ) -> ResourceExecutionResult:
        draft = SetupDraft.from_intent(intent)
        plan_hash = self.review(draft).plan.plan_hash
        return self.apply(draft.model_copy(update={"review_hash": plan_hash}))

    def inspect_intent(
        self,
        intent: SetupIntent,
    ) -> ResourceInspectionResult:
        """Observe a compiled topology without mutating remote or local state."""
        draft = SetupDraft.from_intent(intent)
        review = self.review(draft)
        cluster = self._cluster_loader()
        if not cluster.panel.url or not cluster.panel.api_token:
            raise LocalStateError(
                "Saved panel credentials are missing.",
                hint="Resume setup before checking topology drift.",
            )
        entries = self._entries(draft.server_refs)
        connections = {server_ref: self._connection_builder(entry) for server_ref, entry in entries.items()}
        addresses = {server_ref: entry.host for server_ref, entry in entries.items()}
        panel = self._panel_factory(
            cluster.panel.url,
            cluster.panel.api_token,
        )
        try:
            shadow = cluster.clone()
            drivers = self._build_drivers(
                review.plan,
                shadow,
                panel,
                connections,
                addresses,
                persist=lambda _state: None,
                prepare_node=None,
            )
            return inspect_resource_plan(
                review.plan,
                shadow,
                drivers,
            )
        finally:
            panel.close()

    def apply(self, draft: SetupDraft) -> ResourceExecutionResult:
        review = self.review(draft)
        if not draft.review_hash or draft.review_hash != review.plan.plan_hash:
            raise LocalStateError(
                "The compiled topology no longer matches the reviewed plan.",
                hint="Review the current plan before applying it.",
            )

        cluster = self._cluster_loader()
        cluster.topology_intent = review.intent
        self._persist(cluster)
        entries = self._entries(draft.server_refs)
        connections = {server_ref: self._connection_builder(entry) for server_ref, entry in entries.items()}
        addresses = {server_ref: entry.host for server_ref, entry in entries.items()}

        control = _control_payload(review.plan)
        control_entry = entries[control.server_ref]
        if not self._control_is_ready(cluster, control_entry):
            self._control_bootstrap(
                control,
                review.plan,
                cluster,
                control_entry,
                connections[control.server_ref],
            )
        if not cluster.panel.url or not cluster.panel.api_token:
            raise ResourceReconcileError("Control-plane bootstrap did not produce panel credentials.")

        panel = self._panel_factory(
            cluster.panel.url,
            cluster.panel.api_token,
        )
        try:
            drivers = self._build_drivers(
                review.plan,
                cluster,
                panel=panel,
                connections=connections,
                addresses=addresses,
                persist=self._persist,
                prepare_node=_prepare_node_runtime,
            )
            result = execute_resource_plan(
                review.plan,
                cluster,
                drivers,
                persist=self._persist,
            )
        finally:
            panel.close()
        return result

    def _build_drivers(
        self,
        plan: ResourcePlan,
        cluster: ClusterConfig,
        panel: MeridianPanel,
        connections: Mapping[str, ServerConnection],
        addresses: Mapping[str, str],
        *,
        persist: PersistCluster,
        prepare_node: Callable[
            [ServerConnection, NodeRuntimePayload, str],
            None,
        ]
        | None,
    ) -> ResourceDrivers:
        shared_node_secrets: dict[str, str] = {}
        workloads = WorkloadStateManager(
            cluster,
            persist=persist,
            key_factory=SSHRealityKeyFactory(connections.__getitem__),
        )
        remnawave = RemnawaveDriverContext(
            panel=panel,
            plan=plan,
            cluster=cluster,
            workloads=workloads,
            server_addresses=addresses,
            node_secrets=shared_node_secrets,
        )
        servers = ServerDriverContext(
            plan=plan,
            cluster=cluster,
            panel=panel,
            connection_for=connections.__getitem__,
            server_addresses=addresses,
            node_secrets=shared_node_secrets,
            prepare_node=prepare_node,
        )
        drivers: ResourceDrivers = {}
        drivers.update(build_remnawave_drivers(remnawave))
        drivers.update(build_server_drivers(servers))
        contract_driver = ContractResourceDriver()
        drivers["control_plane_runtime"] = contract_driver
        drivers["probe"] = contract_driver
        return drivers

    def verify(self, draft: SetupDraft) -> SetupVerification:
        review = self.review(draft)
        cluster = self._cluster_loader()
        if (
            not draft.applied_plan_hash
            or cluster.active_plan_hash != review.plan.plan_hash
            or draft.applied_plan_hash != review.plan.plan_hash
        ):
            raise LocalStateError(
                "The reviewed topology has not finished applying.",
                hint="Resume the apply stage before verification.",
            )
        if not cluster.panel.url or not cluster.panel.api_token:
            raise LocalStateError(
                "Saved panel credentials are missing.",
                hint="Resume control-plane setup before verification.",
            )

        panel = self._panel_factory(
            cluster.panel.url,
            cluster.panel.api_token,
        )
        try:
            if not panel.ping():
                raise ResourceReconcileError("The Remnawave control plane did not answer verification.")
            nodes = panel.list_nodes()
            expected_nodes = {
                payload.name
                for resource in review.plan.resources
                if isinstance(
                    (payload := resource.payload),
                    ConfigProfilePayload,
                )
            }
            observed_nodes = {node.name: node for node in nodes if node.name in expected_nodes}
            missing_nodes = sorted(expected_nodes - set(observed_nodes))
            if missing_nodes:
                raise ResourceReconcileError("Managed nodes are missing: " + ", ".join(missing_nodes))
            disconnected = sorted(
                node.name for node in observed_nodes.values() if node.is_disabled or not node.is_connected
            )
            if disconnected:
                raise ResourceReconcileError("Managed nodes are not connected: " + ", ".join(disconnected))

            subscription_urls: dict[str, str] = {}
            for payload in _access_users(review.plan):
                user = panel.get_user(payload.username)
                if user is None or not user.short_uuid:
                    raise ResourceReconcileError(f"Managed access user {payload.username!r} is missing.")
                document = panel.fetch_subscription(user.short_uuid)
                if not document.content.strip():
                    raise ResourceReconcileError(f"Canonical subscription for {payload.username!r} is empty.")
                subscription_urls[payload.username] = document.url
        finally:
            panel.close()

        return SetupVerification(
            plan_hash=review.plan.plan_hash,
            node_count=len(observed_nodes),
            subscription_urls=subscription_urls,
        )

    def _entries(
        self,
        server_refs: list[str],
    ) -> dict[str, ServerEntry]:
        entries: dict[str, ServerEntry] = {}
        for server_ref in server_refs:
            entry = self._registry.find(server_ref)
            if entry is None:
                raise LocalStateError(
                    f"Saved server {server_ref!r} no longer exists.",
                    hint="Go back to the server stage and repair the selection.",
                )
            entries[server_ref] = entry
        return entries

    def _control_is_ready(
        self,
        cluster: ClusterConfig,
        entry: ServerEntry,
    ) -> bool:
        if not cluster.panel.url or not cluster.panel.api_token or cluster.panel.server_ip != entry.host:
            return False
        panel = self._panel_factory(
            cluster.panel.url,
            cluster.panel.api_token,
        )
        try:
            return panel.ping()
        finally:
            panel.close()


def _draft_intent(draft: SetupDraft) -> SetupIntent:
    try:
        return draft.to_intent()
    except ValueError as exc:
        raise LocalStateError(
            str(exc),
            hint="Complete every setup choice before review.",
        ) from exc


def _control_payload(
    plan: ResourcePlan,
) -> ControlPlaneRuntimePayload:
    controls = [
        resource.payload for resource in plan.resources if isinstance(resource.payload, ControlPlaneRuntimePayload)
    ]
    if len(controls) != 1:
        raise ResourceReconcileError("A reviewed topology must contain exactly one control plane.")
    return controls[0]


def _access_users(plan: ResourcePlan) -> list[AccessUserPayload]:
    return [resource.payload for resource in plan.resources if isinstance(resource.payload, AccessUserPayload)]


def _connection_for_entry(entry: ServerEntry) -> ServerConnection:
    return ServerConnection(
        entry.host,
        entry.user,
        port=entry.port,
        identity_file=entry.key_path,
    )


def _bootstrap_control_plane(
    payload: ControlPlaneRuntimePayload,
    plan: ResourcePlan,
    cluster: ClusterConfig,
    entry: ServerEntry,
    connection: ServerConnection,
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

    ports, reality_sni = _bootstrap_profile_values(plan)
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
        harden=True,
        is_panel_host=True,
        secret_path=secret_path,
        xhttp_port=ports["xhttp"],
        reality_port=ports["reality"],
        wss_port=ports["wss"],
        info_page_path=info_page_path,
    )
    ensure_control_plane_access(
        resolved=resolved,
        cluster=cluster,
        domain=payload.public_hostname,
        secret_path=secret_path,
        info_page_path=info_page_path,
        version=__version__,
    )


def _bootstrap_profile_values(
    plan: ResourcePlan,
) -> tuple[dict[str, int], str]:
    ports = {
        "reality": 10443,
        "xhttp": 30443,
        "wss": 20443,
    }
    reality_sni = "www.microsoft.com"
    for resource in plan.resources:
        payload = resource.payload
        if not isinstance(payload, ConfigProfilePayload):
            continue
        for inbound in payload.inbounds:
            if inbound.protocol in ports:
                ports[inbound.protocol] = inbound.listen_port
            if inbound.protocol == "reality":
                reality_sni = inbound.reality_sni
        if payload.workload_kind == "exit":
            break
    return ports, reality_sni


def _prepare_node_runtime(
    connection: ServerConnection,
    payload: NodeRuntimePayload,
    address: str,
) -> None:
    from meridian.provision import (
        ProvisionContext,
        Provisioner,
        build_node_steps,
    )

    context = ProvisionContext(
        ip=address,
        user=connection.user,
        warp=payload.warp,
        harden=True,
        is_panel_host=False,
    )
    results = Provisioner(build_node_steps(context)).run(
        connection,
        context,
    )
    failures = [result for result in results if result.status == "failed"]
    if failures:
        raise ResourceReconcileError(f"Could not prepare node host {address}: {failures[0].name}: {failures[0].detail}")
