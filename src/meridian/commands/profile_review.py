"""The same private gain/loss review serves profile editing and fleet document import."""

from collections.abc import Sequence

from meridian.cluster import ClusterConfig
from meridian.compiler import compile_topology
from meridian.compiler.access_profiles import validate_profile_backend
from meridian.compiler.errors import TopologyCompileError
from meridian.compiler.models import CompiledResource, NodeWorkloadPayload, ResourcePlan, ServiceSettingsPayload
from meridian.console import fail
from meridian.control.client import ControlClient, ControlServiceError, ControlServiceNotFound
from meridian.core.access_profile_views import AccessProfileReview, AccessProfileReviewRequest, GrantedInbound
from meridian.core.client_limits import UserCreationSettings
from meridian.core.errors import InfrastructureEvidenceUnavailableError
from meridian.core.topology import SetupIntent
from meridian.i18n import t


def profile_review_paths(review: AccessProfileReview | None) -> frozenset[str]:
    """Profile identifiers may look like secret keys; their compiled grants remain typed public topology."""
    return (
        frozenset(f"data.access_review.access_profiles.{name}" for name in review.access_profiles)
        if review is not None
        else frozenset()
    )


class ProfileReviewUnavailableError(InfrastructureEvidenceUnavailableError):
    def __init__(self) -> None:
        super().__init__(t("profiles.evidence_unavailable"), hint=t("profiles.evidence_retry"))


def profile_review_needed(
    cluster: ClusterConfig,
    intent: SetupIntent,
    *,
    allow_bootstrap: bool = False,
    reviewed_plan_hash: str = "",
) -> bool:
    """Removing the last profile still reviews the live service's membership and fences its hash."""
    if intent.control.backend != "meridian":
        return False
    if intent.access_profiles:
        return True
    if not cluster.panel.url or not cluster.panel.api_token:
        if allow_bootstrap and _empty_profile_history(cluster, intent, reviewed_plan_hash):
            return False
        raise ProfileReviewUnavailableError()
    try:
        with ControlClient(cluster.panel.url, cluster.panel.api_token) as client:
            return bool(client.settings().access_profiles)
    except ControlServiceError as exc:
        if not _unavailable(exc):
            raise
        if allow_bootstrap and _empty_profile_history(cluster, intent, reviewed_plan_hash):
            return False
        raise ProfileReviewUnavailableError() from exc


def _unavailable(exc: ControlServiceError) -> bool:
    return exc.category == "system" or isinstance(exc, ControlServiceNotFound)


def _empty_profile_history(cluster: ClusterConfig, intent: SetupIntent, reviewed_plan_hash: str) -> bool:
    """Prove empty settings from the saved generation, never from the proposed empty map alone."""
    previous = cluster.topology_intent
    if previous is None or previous.access_profiles:
        return False
    settings = next(
        (row for row in compile_topology(previous).resources if isinstance(row.payload, ServiceSettingsPayload)), None
    )
    if settings is None:
        return False
    same_review = previous == intent and bool(reviewed_plan_hash)
    if cluster.pending_plan_hash:
        if cluster.pending_generation <= 0:
            return False
        pending_empty = same_review and cluster.pending_plan_hash == reviewed_plan_hash
        pending_empty = pending_empty or any(
            checkpoint.resource_id == settings.logical_id
            and checkpoint.generation == cluster.pending_generation
            and checkpoint.expected_hash == settings.desired_hash
            for checkpoint in cluster.action_checkpoints.values()
        )
        if not pending_empty:
            return False
    elif cluster.pending_generation:
        return False
    if cluster.active_generation == 0:
        return not cluster.active_plan_hash and all(
            binding.generation == cluster.pending_generation and cluster.pending_generation > 0
            for binding in cluster.managed_bindings.values()
        )
    if same_review and cluster.active_plan_hash == reviewed_plan_hash:
        return True
    return any(
        binding.logical_id == settings.logical_id
        and binding.resource_kind == "service_settings"
        and binding.active
        and binding.generation == cluster.active_generation
        and binding.desired_hash == settings.desired_hash
        for binding in cluster.managed_bindings.values()
    )


def require_profile_backend(intent: SetupIntent, *, requested: bool = False) -> None:
    try:
        validate_profile_backend(intent, requested=requested)
    except TopologyCompileError:
        fail(t("profiles.migrate_first"), hint_type="user")


def review_profile_access(cluster: ClusterConfig, plan: ResourcePlan) -> AccessProfileReview | None:
    return review_profile_resources(cluster, plan.resources)


def review_profile_resources(
    cluster: ClusterConfig, resources: Sequence[CompiledResource]
) -> AccessProfileReview | None:
    settings = next((row.payload for row in resources if isinstance(row.payload, ServiceSettingsPayload)), None)
    if settings is None:
        return None
    if not cluster.panel.url or not cluster.panel.api_token:
        raise ProfileReviewUnavailableError()
    refs = [
        ref
        for row in resources
        if isinstance(row.payload, NodeWorkloadPayload)
        for ref, inbound in zip(row.payload.profile.inbound_refs, row.payload.profile.inbounds, strict=True)
        if inbound.purpose != "bridge"
    ]
    with ControlClient(cluster.panel.url, cluster.panel.api_token) as client:
        existing = []
        if cluster.topology_intent is not None:
            previous = compile_topology(cluster.topology_intent)
            existing = [
                GrantedInbound(
                    node_id=row.payload.workload_ref, inbound_tag=inbound.tag, inbound_ref=ref, granted_by=[]
                )
                for row in previous.resources
                if isinstance(row.payload, NodeWorkloadPayload)
                for ref, inbound in zip(row.payload.profile.inbound_refs, row.payload.profile.inbounds, strict=True)
                if inbound.purpose != "bridge"
            ]
        try:
            return client.preview_access_profiles(
                AccessProfileReviewRequest(
                    access_profiles=settings.access_profiles, inbound_refs=refs, existing_inbounds=existing
                )
            )
        except ControlServiceError as exc:
            if not _unavailable(exc):
                raise
            raise ProfileReviewUnavailableError() from exc


def export_creation_memberships(cluster: ClusterConfig, document: dict) -> None:
    """Carry live declared memberships as creation-only inputs; existing people keep service ownership."""
    if cluster.topology_intent is None or not cluster.topology_intent.access_profiles:
        return
    access = document["access"]
    settings = dict(access.get("creation_settings", {}))
    with ControlClient(cluster.panel.url, cluster.panel.api_token) as client:
        for user in client.list_users():
            if user.username not in access["users"]:
                continue
            if user.profiles != ["default"] or "profiles" in settings.get(user.username, {}):
                initial = UserCreationSettings.model_validate(
                    {**settings.get(user.username, {}), "profiles": user.profiles}
                )
                settings[user.username] = initial.to_data()
    if settings:
        access["creation_settings"] = settings
