"""Runtime bridge from reviewed setup intent to checkpointed convergence."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import Any, cast
from urllib.parse import urlparse

from meridian import __version__
from meridian.cluster import ClusterConfig, ManagedResourceBinding
from meridian.compiler import (
    DeploymentContract,
    DeploymentTarget,
    compile_topology,
    deployment_server_refs,
)
from meridian.compiler.models import (
    AccessUserPayload,
    ConfigProfilePayload,
    ControlPlaneRuntimePayload,
    ResourcePlan,
    ServerBaselinePayload,
    canonical_hash,
)
from meridian.config import (
    ACME_SERVER,
    REALM_SHA256,
    REALM_VERSION,
    REMNAWAVE_BACKEND_IMAGE,
    REMNAWAVE_NODE_API_PORT,
    REMNAWAVE_NODE_IMAGE,
    REMNAWAVE_PANEL_PORT,
    REMNAWAVE_POSTGRES_IMAGE,
    REMNAWAVE_SUBSCRIPTION_PAGE_IMAGE,
    REMNAWAVE_SUBSCRIPTION_PAGE_PORT,
    REMNAWAVE_VALKEY_IMAGE,
    XRAY_VERSION,
)
from meridian.core.cancellation import CancellationToken
from meridian.core.defaults import DEFAULT_SNI
from meridian.core.errors import LocalStateError
from meridian.core.output import OperationContext
from meridian.core.reporters import NoopReporter, Reporter
from meridian.core.setup import SetupDraft
from meridian.core.topology import SetupIntent
from meridian.handoff import HandoffLinks, publish_connection_pages
from meridian.panel_bootstrap import (
    ensure_control_plane_access,
    run_provisioner,
)
from meridian.provision.ensure import ensure_file_content
from meridian.reconciler.fence import ApplyFence
from meridian.reconciler.remnawave_drivers import (
    RemnawaveDriverContext,
    build_remnawave_drivers,
)
from meridian.reconciler.render_contract import RENDERER_CONTRACT
from meridian.reconciler.resource_executor import (
    environment_after_apply_barrier,
    execute_resource_plan,
    inspect_resource_plan,
    validate_resource_plan_transition,
)
from meridian.reconciler.resources import (
    ResourceAction,
    ResourceDrivers,
    ResourceExecutionResult,
    ResourceInspectionResult,
    ResourceReconcileError,
    UnknownResourceOutcome,
    assert_complete_driver_registry,
)
from meridian.reconciler.retirement import retired_bindings, retirement_review_hash, retiring_server_refs
from meridian.reconciler.runtime_drivers import (
    ControlPlaneDriverContext,
    ControlPlaneRuntimeDriver,
    ProbeDriver,
    ProbeDriverContext,
    xray_subscription_is_valid,
)
from meridian.reconciler.server_drivers import (
    ServerDriverContext,
    build_server_drivers,
)
from meridian.reconciler.server_evidence import file_content_or_absent, require_successful_evidence
from meridian.reconciler.workloads import (
    SSHRealityKeyFactory,
    WorkloadStateManager,
)
from meridian.remnawave import XRAY_JSON_CLIENT_TYPE, MeridianPanel
from meridian.remnawave.versions import check_panel_version
from meridian.resolve import ResolvedServer, connection_for_entry
from meridian.servers import ServerEntry, ServerRegistry
from meridian.setup.legacy_guard import refuse_legacy_deployments
from meridian.ssh import ServerConnection
from meridian.state_fence import SSHApplyFence

PersistCluster = Callable[[ClusterConfig], None]
PanelFactory = Callable[[str, str], MeridianPanel]
ConnectionBuilder = Callable[[ServerEntry], ServerConnection]
FenceFactory = Callable[[ServerConnection], ApplyFence | None]
ControlBootstrap = Callable[
    [ControlPlaneRuntimePayload, ResourcePlan, ClusterConfig, ServerEntry, ServerConnection],
    None,
]
_CONTROL_ATTESTATION_PATH = "/var/lib/meridian/control-runtime.attestation"


@dataclass(frozen=True)
class SetupReview:
    """Deterministic review boundary shown before any remote mutation."""

    intent: SetupIntent
    plan: ResourcePlan
    retiring: tuple[ManagedResourceBinding, ...] = ()
    retirement_targets: Mapping[str, DeploymentTarget] = field(default_factory=dict)

    @property
    def review_hash(self) -> str:
        """The hash an operator approves: the plan plus exactly what it removes."""
        return retirement_review_hash(self.plan.plan_hash, list(self.retiring), self.retirement_targets)


@dataclass(frozen=True)
class SetupVerification:
    """Canonical post-apply evidence used by handoff presentation."""

    plan_hash: str
    node_count: int
    subscription_urls: Mapping[str, str]
    panel_version: str = ""
    warnings: tuple[str, ...] = ()


class LazyPanel:
    """Open the panel adapter only after checkpointed control bootstrap."""

    def __init__(
        self,
        factory: PanelFactory,
        cluster: ClusterConfig,
    ) -> None:
        self._factory = factory
        self._cluster = cluster
        self._panel: MeridianPanel | None = None

    def close(self) -> None:
        if self._panel is not None:
            self._panel.close()

    def reset(self) -> None:
        self.close()
        self._panel = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get(), name)

    def _get(self) -> MeridianPanel:
        if self._panel is None:
            if not self._cluster.panel.url or not self._cluster.panel.api_token:
                raise ResourceReconcileError("Control-plane credentials are unavailable before bootstrap.")
            self._panel = self._factory(
                self._cluster.panel.url,
                self._cluster.panel.api_token,
            )
        return self._panel


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
        cancellation: CancellationToken | None = None,
        reporter: Reporter | None = None,
        operation: OperationContext | None = None,
        fence_factory: FenceFactory | None = None,
    ) -> None:
        self._registry = registry
        self._fence_factory: FenceFactory = fence_factory or SSHApplyFence
        self._cluster_loader = cluster_loader
        self._persist = persist or (lambda cluster: cluster.save())
        self._panel_factory = panel_factory
        self._connection_builder = connection_builder or connection_for_entry
        self._cancellation = cancellation
        self._reporter: Reporter = reporter or NoopReporter()
        self._operation = operation or OperationContext()
        self._control_bootstrap = control_bootstrap or partial(
            _bootstrap_control_plane,
            cancellation=cancellation,
            reporter=self._reporter,
            operation=self._operation,
        )

    def review(self, draft: SetupDraft, *, cluster: ClusterConfig | None = None) -> SetupReview:
        """Compile the draft and list the saved resources the new plan would remove."""
        intent = _draft_intent(draft)
        entries = self._entries(sorted(deployment_server_refs(intent)))
        contract = DeploymentContract(
            server_targets={server_ref: _deployment_target(entry) for server_ref, entry in entries.items()},
            runtime_pins={
                "acme_server": ACME_SERVER,
                "meridian_version": __version__,
                "realm_sha256_manifest": canonical_hash(REALM_SHA256),
                "realm_version": REALM_VERSION,
                "remnawave_backend_image": REMNAWAVE_BACKEND_IMAGE,
                "remnawave_node_image": REMNAWAVE_NODE_IMAGE,
                "remnawave_node_api_port": str(REMNAWAVE_NODE_API_PORT),
                "remnawave_panel_port": str(REMNAWAVE_PANEL_PORT),
                "remnawave_postgres_image": REMNAWAVE_POSTGRES_IMAGE,
                "remnawave_subscription_page_image": REMNAWAVE_SUBSCRIPTION_PAGE_IMAGE,
                "remnawave_subscription_page_port": str(REMNAWAVE_SUBSCRIPTION_PAGE_PORT),
                "remnawave_valkey_image": REMNAWAVE_VALKEY_IMAGE,
                "xray_version": XRAY_VERSION,
            },
            renderer_contract=RENDERER_CONTRACT,
        )
        plan = compile_topology(intent, deployment_contract=contract)
        retiring = tuple(retired_bindings(plan, cluster if cluster is not None else self._cluster_loader()))
        targets = {
            server_ref: contract.server_targets[server_ref]
            for server_ref in retiring_server_refs(retiring)
            if server_ref in contract.server_targets
        }
        for server_ref, entry in self._retirement_entries(retiring, plan).items():
            targets[server_ref] = _deployment_target(entry)
        return SetupReview(intent=intent, plan=plan, retiring=retiring, retirement_targets=targets)

    def apply_intent(
        self,
        intent: SetupIntent,
        *,
        expected_plan_hash: str | None = None,
    ) -> ResourceExecutionResult:
        draft = SetupDraft.from_intent(intent)
        review_hash = self.review(draft).review_hash
        if expected_plan_hash is not None and review_hash != expected_plan_hash:
            raise LocalStateError(
                "The compiled topology changed after it was reviewed.",
                hint="Review the current plan before applying it.",
            )
        return self.apply(draft.model_copy(update={"review_hash": review_hash}))

    def inspect_intent(
        self,
        intent: SetupIntent,
    ) -> ResourceInspectionResult:
        """Observe a compiled topology without mutating remote or local state."""
        draft = SetupDraft.from_intent(intent)
        cluster = self._cluster_loader()
        review = self.review(draft, cluster=cluster)
        if not cluster.panel.url or not cluster.panel.api_token:
            raise LocalStateError(
                "Saved panel credentials are missing.",
                hint="Resume setup before checking topology drift.",
            )
        entries = self._entries(sorted(review.plan.deployment_contract.server_targets))
        connections = {server_ref: self._connection_builder(entry) for server_ref, entry in entries.items()}
        addresses = {server_ref: entry.host for server_ref, entry in entries.items()}
        panel = self._panel_factory(
            cluster.panel.url,
            cluster.panel.api_token,
        )
        control = _control_payload(review.plan)
        control_entry = entries[control.server_ref]
        control_connection = connections[control.server_ref]
        try:
            shadow = cluster.clone()
            drivers = self._build_drivers(
                review.plan,
                shadow,
                panel,
                connections,
                addresses,
                persist=lambda _state: None,
                control_context=ControlPlaneDriverContext(
                    ready=lambda action: _control_runtime_ready(
                        action,
                        review.plan,
                        cluster,
                        control_entry,
                        control_connection,
                        panel,
                    ),
                    bootstrap=lambda _action: _inspection_bootstrap_error(),
                ),
            )
            inspection = inspect_resource_plan(
                review.plan,
                shadow,
                drivers,
            )
            return inspection.model_copy(update={"review_hash": review.review_hash})
        finally:
            panel.close()

    def apply(self, draft: SetupDraft) -> ResourceExecutionResult:
        cluster = self._cluster_loader()
        review = self.review(draft, cluster=cluster)
        if not draft.review_hash or draft.review_hash != review.review_hash:
            raise LocalStateError(
                "The compiled topology no longer matches the reviewed plan.",
                hint="Review the current plan before applying it.",
            )

        validate_resource_plan_transition(review.plan, cluster)
        targets = sorted(review.plan.deployment_contract.server_targets)
        entries = {
            **self._retirement_entries(review.retiring, review.plan),
            **self._entries(targets),
        }
        connections = {server_ref: self._connection_builder(entry) for server_ref, entry in entries.items()}
        addresses = {server_ref: entry.host for server_ref, entry in entries.items()}
        refuse_legacy_deployments({ref: connections[ref] for ref in targets}, addresses)
        cluster.topology_intent = review.intent
        self._persist(cluster)

        control = _control_payload(review.plan)
        control_entry = entries[control.server_ref]
        panel = LazyPanel(self._panel_factory, cluster)

        def bootstrap_control(action: ResourceAction) -> None:
            self._control_bootstrap(
                control,
                review.plan,
                cluster,
                control_entry,
                connections[control.server_ref],
            )
            _write_control_runtime_attestation(action, review.plan, connections[control.server_ref])
            panel.reset()

        try:
            drivers = self._build_drivers(
                review.plan,
                cluster,
                panel=cast(MeridianPanel, panel),
                connections=connections,
                addresses=addresses,
                persist=self._persist,
                control_context=ControlPlaneDriverContext(
                    ready=lambda action: _control_runtime_ready(
                        action,
                        review.plan,
                        cluster,
                        control_entry,
                        connections[control.server_ref],
                        cast(MeridianPanel, panel),
                    ),
                    bootstrap=bootstrap_control,
                ),
            )
            result = execute_resource_plan(
                review.plan,
                cluster,
                drivers,
                persist=self._persist,
                after_apply=environment_after_apply_barrier,
                cancellation=self._cancellation,
                reporter=self._reporter,
                operation=self._operation,
                fence=self._fence_factory(connections[control.server_ref]),
            )
        finally:
            panel.close()
        return result.model_copy(update={"review_hash": review.review_hash})

    def _build_drivers(
        self,
        plan: ResourcePlan,
        cluster: ClusterConfig,
        panel: MeridianPanel,
        connections: Mapping[str, ServerConnection],
        addresses: Mapping[str, str],
        *,
        persist: PersistCluster,
        control_context: ControlPlaneDriverContext,
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
            cancellation=self._cancellation,
            reporter=self._reporter,
            operation=self._operation,
        )
        drivers: ResourceDrivers = {}
        drivers.update(build_remnawave_drivers(remnawave))
        drivers.update(build_server_drivers(servers))
        drivers["control_plane_runtime"] = ControlPlaneRuntimeDriver(control_context)
        drivers["probe"] = ProbeDriver(
            ProbeDriverContext(
                plan=plan,
                cluster=cluster,
                panel=panel,
                connection_for=connections.__getitem__,
                server_addresses=addresses,
            )
        )
        assert_complete_driver_registry(drivers)
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
            panel_version = panel.get_version()
            version_warning = check_panel_version(panel_version)
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
                document = panel.fetch_subscription(user.short_uuid, client_type=XRAY_JSON_CLIENT_TYPE)
                if not xray_subscription_is_valid(document):
                    raise ResourceReconcileError(f"Canonical subscription for {payload.username!r} is invalid.")
                subscription_urls[payload.username] = cluster.panel.subscription_url(user.short_uuid)
        finally:
            panel.close()

        return SetupVerification(
            plan_hash=review.plan.plan_hash,
            node_count=len(observed_nodes),
            subscription_urls=subscription_urls,
            panel_version=panel_version,
            warnings=(version_warning,) if version_warning else (),
        )

    def publish_handoff(self, usernames: Sequence[str]) -> list[HandoffLinks]:
        """Publish connection pages for verified users and return their links."""
        return publish_connection_pages(self._cluster_loader(), usernames)

    def _retirement_entries(
        self,
        retiring: Sequence[ManagedResourceBinding],
        plan: ResourcePlan,
    ) -> dict[str, ServerEntry]:
        """Saved profiles for servers that only resources being removed still reference."""
        entries: dict[str, ServerEntry] = {}
        for server_ref in sorted(retiring_server_refs(retiring) - set(plan.deployment_contract.server_targets)):
            entry = self._registry.find(server_ref)
            if entry is None:
                raise LocalStateError(
                    f"Server {server_ref!r} still runs resources this change removes, but its saved profile is gone.",
                    hint="Add the server back with its SSH details so Meridian can clean it up, then review again.",
                )
            entries[server_ref] = entry
        return entries

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


def _draft_intent(draft: SetupDraft) -> SetupIntent:
    try:
        return draft.to_intent()
    except ValueError as exc:
        raise LocalStateError(
            str(exc),
            hint="Complete every setup choice before review.",
        ) from exc


def _inspection_bootstrap_error() -> None:
    raise ResourceReconcileError("Read-only inspection cannot bootstrap the control plane.")


def _control_runtime_ready(
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
    attestation = connection.get_text(_CONTROL_ATTESTATION_PATH, timeout=15)
    expected_attestation = _control_runtime_attestation(action, plan)
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


def _write_control_runtime_attestation(
    action: ResourceAction,
    plan: ResourcePlan,
    connection: ServerConnection,
) -> None:
    result = ensure_file_content(
        connection,
        _CONTROL_ATTESTATION_PATH,
        f"{_control_runtime_attestation(action, plan)}\n",
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


def _control_runtime_attestation(action: ResourceAction, plan: ResourcePlan) -> str:
    """Hash only what the bootstrap consumes, so other servers joining or leaving never re-run it."""
    server_ref = _control_payload(plan).server_ref
    contract = plan.deployment_contract
    target = contract.server_targets.get(server_ref)
    ports, reality_sni = _bootstrap_profile_values(plan)
    return canonical_hash(
        {
            "resource": action.expected_hash,
            "control_target": target.model_dump(mode="json") if target is not None else None,
            "runtime_pins": contract.runtime_pins,
            "renderer_contract": contract.renderer_contract,
            "bootstrap_profile": {"ports": ports, "reality_sni": reality_sni},
            "harden": _plan_harden(plan, server_ref),
        }
    )


def _socket_table_has_port(output: str, port: int) -> bool:
    suffix = f":{port}"
    return any(field.endswith(suffix) for line in output.splitlines() for field in line.split())


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


def _deployment_target(entry: ServerEntry) -> DeploymentTarget:
    return DeploymentTarget(host=entry.host, user=entry.user, port=entry.port)


def _bootstrap_control_plane(
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
        harden=_plan_harden(plan, payload.server_ref),
        is_panel_host=True,
        secret_path=secret_path,
        xhttp_port=ports["xhttp"],
        reality_port=ports["reality"],
        wss_port=ports["wss"],
        info_page_path=info_page_path,
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


def _plan_harden(plan: ResourcePlan, server_ref: str) -> bool:
    """Whether the reviewed baseline hardens this server (SSH key-only, fail2ban)."""
    return next(
        (
            resource.payload.harden
            for resource in plan.resources
            if isinstance(resource.payload, ServerBaselinePayload) and resource.payload.server_ref == server_ref
        ),
        True,
    )


def _bootstrap_profile_values(
    plan: ResourcePlan,
) -> tuple[dict[str, int], str]:
    ports = {
        "reality": 10443,
        "xhttp": 30443,
        "wss": 20443,
    }
    reality_sni = DEFAULT_SNI
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
