"""Runtime bridge from reviewed setup intent to checkpointed convergence."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import Any, cast

from meridian import __version__
from meridian.binary_pins import REALM_SHA256_PIN_PREFIX, effective_binary_pins
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
    ControlServicePayload,
    ResourcePlan,
)
from meridian.config import (
    ACME_SERVER,
    REMNAWAVE_NODE_API_PORT,
    REMNAWAVE_PANEL_PORT,
    REMNAWAVE_POSTGRES_IMAGE,
    REMNAWAVE_SUBSCRIPTION_PAGE_PORT,
    REMNAWAVE_VALKEY_IMAGE,
    XRAY_VERSION,
)
from meridian.core.cancellation import CancellationToken
from meridian.core.errors import LocalStateError
from meridian.core.output import OperationContext
from meridian.core.reporters import NoopReporter, Reporter
from meridian.core.setup import SetupDraft
from meridian.core.topology import SetupIntent
from meridian.fleet_contexts import refuse_if_superseded, refuse_servers_claimed_elsewhere
from meridian.handoff import HandoffLinks, publish_connection_pages
from meridian.reconciler.fence import ApplyFence, local_fence_view
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
from meridian.reconciler.stall_retry import stall_event_notice
from meridian.reconciler.workloads import (
    PanelRealityKeyFactory,
    WorkloadStateManager,
)
from meridian.remnawave import XRAY_JSON_CLIENT_TYPE, MeridianPanel
from meridian.remnawave.versions import check_panel_version, effective_pins
from meridian.resolve import connection_for_entry
from meridian.servers import ServerEntry, ServerRegistry
from meridian.service_fence import ControlServiceFence, RevisionDraft
from meridian.setup.control_plane import (
    bootstrap_control_plane,
    control_payload,
    control_runtime_ready,
    write_control_runtime_attestation,
)
from meridian.setup.control_service import (
    bootstrap_reviewed_control_service,
    control_service_ready,
    meridian_drivers,
    verify_control_service,
)
from meridian.setup.legacy_guard import refuse_legacy_deployments
from meridian.ssh import ServerConnection
from meridian.state_fence import SSHApplyFence

PersistCluster = Callable[[ClusterConfig], None]
PanelFactory = Callable[[str, str], MeridianPanel]
ConnectionBuilder = Callable[[ServerEntry], ServerConnection]
# The control server's connection, the cluster, and the reviewed change the fence guards.
FenceFactory = Callable[[ServerConnection, ClusterConfig, "SetupReview"], ApplyFence | None]
ControlBootstrap = Callable[
    [ControlPlaneRuntimePayload, ResourcePlan, ClusterConfig, ServerEntry, ServerConnection],
    None,
]


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
    panel_url: str = ""
    admin_user: str = ""  # the saved login; the password stays behind `meridian panel --show-password`


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
        fence_holder: str | None = None,
    ) -> None:
        self._registry = registry
        self._cluster_loader = cluster_loader
        self._persist = persist or (lambda cluster: cluster.save())
        self._panel_factory = panel_factory
        self._connection_builder = connection_builder or connection_for_entry
        self._cancellation = cancellation
        self._reporter: Reporter = reporter or NoopReporter()
        self._operation = operation or OperationContext()
        self._fence_factory: FenceFactory = fence_factory or self._default_fence
        # A caller already holding the lease (`meridian migrate`) applies under the same holder.
        self._fence_holder = fence_holder
        self._control_bootstrap = control_bootstrap or partial(
            bootstrap_control_plane,
            cancellation=cancellation,
            reporter=self._reporter,
            operation=self._operation,
        )

    def review(self, draft: SetupDraft, *, cluster: ClusterConfig | None = None) -> SetupReview:
        """Compile the draft and list the saved resources the new plan would remove."""
        cluster = cluster if cluster is not None else self._cluster_loader()
        intent = _with_fleet_backend(_draft_intent(draft), cluster)
        entries = self._entries(sorted(deployment_server_refs(intent)))
        images = effective_pins(cluster.image_pins)
        realm = effective_binary_pins(cluster.binary_pins)["realm"]
        contract = DeploymentContract(
            server_targets={server_ref: _deployment_target(entry) for server_ref, entry in entries.items()},
            runtime_pins={
                "acme_server": ACME_SERVER,
                "meridian_version": __version__,
                **{f"{REALM_SHA256_PIN_PREFIX}{target}": digest for target, digest in sorted(realm.sha256.items())},
                "realm_version": realm.version,
                "remnawave_backend_image": images["remnawave_backend_image"],
                "remnawave_node_image": images["remnawave_node_image"],
                "remnawave_node_api_port": str(REMNAWAVE_NODE_API_PORT),
                "remnawave_panel_port": str(REMNAWAVE_PANEL_PORT),
                "remnawave_postgres_image": REMNAWAVE_POSTGRES_IMAGE,
                "remnawave_subscription_page_image": images["remnawave_subscription_page_image"],
                "remnawave_subscription_page_port": str(REMNAWAVE_SUBSCRIPTION_PAGE_PORT),
                "remnawave_valkey_image": REMNAWAVE_VALKEY_IMAGE,
                "xray_version": XRAY_VERSION,
            },
            renderer_contract=RENDERER_CONTRACT,
        )
        plan = compile_topology(intent, deployment_contract=contract)
        retiring = tuple(retired_bindings(plan, cluster))
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
        control = control_payload(review.plan)
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
                    ready=lambda action: (
                        control_service_ready(action, review.plan, cluster, control_entry, control_connection)
                        if isinstance(control, ControlServicePayload)
                        else control_runtime_ready(
                            action,
                            review.plan,
                            cluster,
                            control_entry,
                            control_connection,
                            panel,
                        )
                    ),
                    bootstrap=lambda _action: _inspection_bootstrap_error(),
                ),
            )
            inspection = inspect_resource_plan(
                review.plan,
                shadow,
                drivers,
            )
            fence = self._fence_factory(control_connection, cluster, review)
            fence_warning = fence.check(local_fence_view(cluster)) if fence is not None else ""
            return inspection.model_copy(update={"review_hash": review.review_hash, "fence_warning": fence_warning})
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

        refuse_if_superseded()
        validate_resource_plan_transition(review.plan, cluster)
        targets = sorted(review.plan.deployment_contract.server_targets)
        entries = {
            **self._retirement_entries(review.retiring, review.plan),
            **self._entries(targets),
        }
        refuse_servers_claimed_elsewhere(entries[ref].host for ref in targets)
        connections = {server_ref: self._connection_builder(entry) for server_ref, entry in entries.items()}
        addresses = {server_ref: entry.host for server_ref, entry in entries.items()}
        refuse_legacy_deployments({ref: connections[ref] for ref in targets}, addresses)
        control = control_payload(review.plan)
        fence = self._fence_factory(connections[control.server_ref], cluster, review)
        if fence is not None:
            # Refuse a stale copy before its intent edit is saved, not only before the first action.
            fence.ensure(local_fence_view(cluster))
        cluster.topology_intent = review.intent
        self._persist(cluster)

        control_entry = entries[control.server_ref]
        panel = LazyPanel(self._panel_factory, cluster)

        def bootstrap_control(action: ResourceAction) -> None:
            if isinstance(control, ControlServicePayload):
                bootstrap_reviewed_control_service(
                    control,
                    review.plan,
                    cluster,
                    control_entry,
                    connections[control.server_ref],
                    cancellation=self._cancellation,
                    reporter=self._reporter,
                    operation=self._operation,
                )
                return
            self._control_bootstrap(
                control,
                review.plan,
                cluster,
                control_entry,
                connections[control.server_ref],
            )
            write_control_runtime_attestation(action, review.plan, connections[control.server_ref])
            panel.reset()

        def control_ready(action: ResourceAction) -> bool:
            if isinstance(control, ControlServicePayload):
                return control_service_ready(
                    action, review.plan, cluster, control_entry, connections[control.server_ref]
                )
            return control_runtime_ready(
                action,
                review.plan,
                cluster,
                control_entry,
                connections[control.server_ref],
                cast(MeridianPanel, panel),
            )

        try:
            drivers = self._build_drivers(
                review.plan,
                cluster,
                panel=cast(MeridianPanel, panel),
                connections=connections,
                addresses=addresses,
                persist=self._persist,
                control_context=ControlPlaneDriverContext(ready=control_ready, bootstrap=bootstrap_control),
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
                fence=fence,
            )
        finally:
            panel.close()
        return result.model_copy(update={"review_hash": review.review_hash})

    def _default_fence(self, connection: ServerConnection, cluster: ClusterConfig, review: SetupReview) -> ApplyFence:
        """fence.json on a Remnawave fleet; the control service, recording the revision, on a Meridian one."""
        on_stall = stall_event_notice(self._reporter, self._operation)
        if review.intent.control.backend != "meridian":
            return SSHApplyFence(connection, holder=self._fence_holder, on_stall=on_stall)
        draft = RevisionDraft.from_intent(
            review.intent, plan_hash=review.plan.plan_hash, review_hash=review.review_hash
        )
        return ControlServiceFence(connection, cluster, draft, holder=self._fence_holder, on_stall=on_stall)

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
            key_factory=PanelRealityKeyFactory(panel),
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
        drivers["control_service"] = ControlPlaneRuntimeDriver(control_context)
        drivers.update(
            meridian_drivers(
                plan,
                cluster,
                persist=persist,
                connections=connections,
                addresses=addresses,
            )
        )
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
        if isinstance(control_payload(review.plan), ControlServicePayload):
            service_version, node_count, urls = verify_control_service(review.plan, cluster)
            return SetupVerification(
                plan_hash=review.plan.plan_hash,
                node_count=node_count,
                subscription_urls=urls,
                panel_version=service_version,
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
            panel_url=cluster.panel.display_url,
            admin_user=cluster.panel.admin_user,
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


def _with_fleet_backend(intent: SetupIntent, cluster: ClusterConfig) -> SetupIntent:
    """Keep a control-service fleet on its backend when a draft leaves it unset.

    The wizard re-asks roles without the backend (chosen once, at deploy), and an intent file
    omits the default; either would otherwise compile a Remnawave plan for a service fleet.
    """
    if not cluster.runs_control_service or intent.control.backend == "meridian":
        return intent
    control = intent.control.model_copy(update={"backend": "meridian"})
    return SetupIntent.model_validate({**intent.model_dump(), "control": control.model_dump()})


def _inspection_bootstrap_error() -> None:
    raise ResourceReconcileError("Read-only inspection cannot bootstrap the control plane.")


def _access_users(plan: ResourcePlan) -> list[AccessUserPayload]:
    return [resource.payload for resource in plan.resources if isinstance(resource.payload, AccessUserPayload)]


def _deployment_target(entry: ServerEntry) -> DeploymentTarget:
    return DeploymentTarget(host=entry.host, user=entry.user, port=entry.port)
