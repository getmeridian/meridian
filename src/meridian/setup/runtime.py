"""Runtime bridge from reviewed setup intent to checkpointed convergence."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial

from meridian import __version__
from meridian.binary_pins import REALM_SHA256_PIN_PREFIX, effective_binary_pins
from meridian.cluster import ClusterConfig, ManagedResourceBinding
from meridian.compiler import (
    DeploymentContract,
    DeploymentTarget,
    compile_topology,
    deployment_server_refs,
)
from meridian.compiler.models import ControlServicePayload, ResourcePlan
from meridian.config import ACME_SERVER, XRAY_VERSION
from meridian.control.command_models import NodeCommandRecord
from meridian.core.cancellation import CancellationToken
from meridian.core.errors import LocalStateError
from meridian.core.output import OperationContext
from meridian.core.reporters import NoopReporter, Reporter
from meridian.core.setup import SetupDraft
from meridian.core.topology import SetupIntent
from meridian.fleet_contexts import refuse_if_superseded, refuse_servers_claimed_elsewhere
from meridian.handoff import HandoffLinks, client_handoffs
from meridian.reconciler.control_service_driver import ControlServiceDriver, ControlServiceDriverContext
from meridian.reconciler.fence import ApplyFence, local_fence_view
from meridian.reconciler.render_contract import RENDERER_CONTRACT
from meridian.reconciler.resource_executor import (
    environment_after_apply_barrier,
    execute_resource_plan,
    inspect_resource_plan,
    validate_resource_plan_transition,
)
from meridian.reconciler.resources import (
    ResourceDrivers,
    ResourceExecutionResult,
    ResourceInspectionResult,
    ResourceReconcileError,
    assert_complete_driver_registry,
)
from meridian.reconciler.retirement import retired_bindings, retirement_review_hash, retiring_server_refs
from meridian.reconciler.server_drivers import (
    ServerDriverContext,
    build_server_drivers,
)
from meridian.reconciler.stall_retry import stall_event_notice
from meridian.resolve import connection_for_entry
from meridian.servers import ServerEntry, ServerRegistry
from meridian.service_fence import ControlServiceFence, RevisionDraft
from meridian.setup.control_service import (
    bootstrap_reviewed_control_service,
    control_payload,
    control_service_ready,
    meridian_drivers,
    verify_control_service,
)
from meridian.setup.legacy_guard import refuse_legacy_deployments
from meridian.ssh import ServerConnection

PersistCluster = Callable[[ClusterConfig], None]
ConnectionBuilder = Callable[[ServerEntry], ServerConnection]
# The control server's connection, the cluster, and the reviewed change the fence guards.
FenceFactory = Callable[[ServerConnection, ClusterConfig, "SetupReview"], ApplyFence | None]
ControlBootstrap = Callable[
    [ControlServicePayload, ResourcePlan, ClusterConfig, ServerEntry, ServerConnection],
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


class SetupRuntime:
    """Compile, converge, and verify one resumable setup draft."""

    def __init__(
        self,
        registry: ServerRegistry,
        *,
        cluster_loader: Callable[[], ClusterConfig] = ClusterConfig.load,
        persist: PersistCluster | None = None,
        connection_builder: ConnectionBuilder | None = None,
        control_bootstrap: ControlBootstrap | None = None,
        cancellation: CancellationToken | None = None,
        reporter: Reporter | None = None,
        operation: OperationContext | None = None,
        fence_factory: FenceFactory | None = None,
        fence_holder: str | None = None,
        user_cut_off: Mapping[str, list[NodeCommandRecord]] | None = None,
    ) -> None:
        self._registry = registry
        self._cluster_loader = cluster_loader
        self._persist = persist or (lambda cluster: cluster.save())
        self._connection_builder = connection_builder or connection_for_entry
        self._cancellation = cancellation
        self._reporter: Reporter = reporter or NoopReporter()
        self._operation = operation or OperationContext()
        self._fence_factory: FenceFactory = fence_factory or self._default_fence
        # A caller already holding the lease (`meridian migrate`) applies under the same holder.
        self._fence_holder = fence_holder
        self._user_cut_off = user_cut_off if user_cut_off is not None else {}
        self._control_bootstrap = control_bootstrap or partial(
            bootstrap_reviewed_control_service,
            cancellation=cancellation,
            reporter=self._reporter,
            operation=self._operation,
        )

    def review(self, draft: SetupDraft, *, cluster: ClusterConfig | None = None) -> SetupReview:
        """Compile the draft and list the saved resources the new plan would remove."""
        cluster = cluster if cluster is not None else self._cluster_loader()
        intent = _draft_intent(draft)
        entries = self._entries(sorted(deployment_server_refs(intent)))
        realm = effective_binary_pins(cluster.binary_pins)["realm"]
        contract = DeploymentContract(
            server_targets={server_ref: _deployment_target(entry) for server_ref, entry in entries.items()},
            runtime_pins={
                "acme_server": ACME_SERVER,
                "meridian_version": __version__,
                **{f"{REALM_SHA256_PIN_PREFIX}{target}": digest for target, digest in sorted(realm.sha256.items())},
                "realm_version": realm.version,
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
                "Saved control-service credentials are missing.",
                hint="Resume setup before checking topology drift.",
            )
        entries = self._entries(sorted(review.plan.deployment_contract.server_targets))
        connections = {server_ref: self._connection_builder(entry) for server_ref, entry in entries.items()}
        addresses = {server_ref: entry.host for server_ref, entry in entries.items()}
        control = control_payload(review.plan)
        control_entry = entries[control.server_ref]
        control_connection = connections[control.server_ref]
        shadow = cluster.clone()
        drivers = self._build_drivers(
            review.plan,
            shadow,
            connections,
            addresses,
            persist=lambda _state: None,
            control_context=ControlServiceDriverContext(
                ready=lambda action: control_service_ready(
                    action, review.plan, cluster, control_entry, control_connection
                ),
                bootstrap=lambda _action: _inspection_bootstrap_error(),
            ),
        )
        inspection = inspect_resource_plan(review.plan, shadow, drivers)
        fence = self._fence_factory(control_connection, cluster, review)
        fence_warning = fence.check(local_fence_view(cluster)) if fence is not None else ""
        return inspection.model_copy(update={"review_hash": review.review_hash, "fence_warning": fence_warning})

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
        control_connection = connections[control.server_ref]
        drivers = self._build_drivers(
            review.plan,
            cluster,
            connections=connections,
            addresses=addresses,
            persist=self._persist,
            control_context=ControlServiceDriverContext(
                ready=lambda action: control_service_ready(
                    action, review.plan, cluster, control_entry, control_connection
                ),
                bootstrap=lambda _action: self._control_bootstrap(
                    control, review.plan, cluster, control_entry, control_connection
                ),
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
            fence=fence,
        )
        return result.model_copy(update={"review_hash": review.review_hash})

    def _default_fence(self, connection: ServerConnection, cluster: ClusterConfig, review: SetupReview) -> ApplyFence:
        """The control service keeps the fence and records the reviewed apply as a revision."""
        on_stall = stall_event_notice(self._reporter, self._operation)
        draft = RevisionDraft.from_intent(
            review.intent, plan_hash=review.plan.plan_hash, review_hash=review.review_hash
        )
        return ControlServiceFence(connection, cluster, draft, holder=self._fence_holder, on_stall=on_stall)

    def _build_drivers(
        self,
        plan: ResourcePlan,
        cluster: ClusterConfig,
        connections: Mapping[str, ServerConnection],
        addresses: Mapping[str, str],
        *,
        persist: PersistCluster,
        control_context: ControlServiceDriverContext,
    ) -> ResourceDrivers:
        servers = ServerDriverContext(
            plan=plan,
            cluster=cluster,
            connection_for=connections.__getitem__,
            server_addresses=addresses,
            cancellation=self._cancellation,
            reporter=self._reporter,
            operation=self._operation,
        )
        drivers: ResourceDrivers = {}
        drivers.update(build_server_drivers(servers))
        drivers["control_service"] = ControlServiceDriver(control_context)
        drivers.update(
            meridian_drivers(
                plan,
                cluster,
                persist=persist,
                connections=connections,
                addresses=addresses,
                user_cut_off=self._user_cut_off,
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
                "Saved control-service credentials are missing.",
                hint="Resume control-service setup before verification.",
            )
        service_version, node_count, urls = verify_control_service(review.plan, cluster)
        return SetupVerification(
            plan_hash=review.plan.plan_hash,
            node_count=node_count,
            subscription_urls=urls,
            panel_version=service_version,
        )

    def handoff(self, usernames: Sequence[str]) -> list[HandoffLinks]:
        """Read connection links for verified users from the control service."""
        return client_handoffs(self._cluster_loader(), usernames)

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
    raise ResourceReconcileError("Read-only inspection cannot bootstrap the control service.")


def _deployment_target(entry: ServerEntry) -> DeploymentTarget:
    return DeploymentTarget(host=entry.host, user=entry.user, port=entry.port)
