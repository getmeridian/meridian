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


def profile_review_needed(cluster: ClusterConfig, intent: SetupIntent, *, allow_bootstrap: bool = False) -> bool:
    """Removing the last profile still reviews the live service's membership and fences its hash.

    With the service unreachable, plan reports unavailable evidence. Apply may proceed
    without that review only if the fleet has never used named access profiles.
    """
    if intent.control.backend != "meridian":
        return False
    if intent.access_profiles:
        return True
    if not cluster.panel.url or not cluster.panel.api_token:
        if allow_bootstrap and not cluster.access_profiles_used:
            return False
        raise ProfileReviewUnavailableError()
    try:
        with ControlClient(cluster.panel.url, cluster.panel.api_token) as client:
            return bool(client.settings().access_profiles)
    except ControlServiceError as exc:
        if not _unavailable(exc):
            raise
        if allow_bootstrap and not cluster.access_profiles_used:
            return False
        raise ProfileReviewUnavailableError() from exc


def _unavailable(exc: ControlServiceError) -> bool:
    return exc.category == "system" or isinstance(exc, ControlServiceNotFound)


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
