"""Shared profile reviews and the declaration check before any apply can write remotely."""

from __future__ import annotations

from collections.abc import Sequence

from meridian.cluster import ClusterConfig
from meridian.compiler import compile_topology
from meridian.compiler.models import CompiledResource, NodeWorkloadPayload, ResourcePlan, ServiceSettingsPayload
from meridian.control.client import ControlClient, ControlServiceError, ControlServiceNotFound
from meridian.core.access_profile_views import (
    AccessProfileCatalog,
    AccessProfileReview,
    AccessProfileReviewRequest,
    GrantedInbound,
)
from meridian.core.access_profiles import profile_declarations_hash
from meridian.core.errors import InfrastructureEvidenceUnavailableError, LocalStateError
from meridian.core.topology import SetupIntent
from meridian.i18n import t


class ProfileReviewUnavailableError(InfrastructureEvidenceUnavailableError):
    def __init__(self) -> None:
        super().__init__(t("profiles.evidence_unavailable"), hint=t("profiles.evidence_retry"))


def check_profile_access(cluster: ClusterConfig, intent: SetupIntent, *, access_review_hash: str | None) -> bool:
    """Refuse declaration changes before the fence; return whether the service has held profiles."""
    if intent.control.backend != "meridian":
        return False
    evidence_required = cluster.has_used_access_profiles
    if not cluster.is_configured:
        if evidence_required:
            raise ProfileReviewUnavailableError()
        return False
    try:
        with ControlClient(cluster.panel.url, cluster.panel.api_token) as client:
            live = client.access_profiles()
    except ControlServiceError as exc:
        if not _unavailable(exc):
            raise
        if evidence_required:
            raise ProfileReviewUnavailableError() from exc
        return False
    held_profiles = _held_profiles(live)
    if (
        access_review_hash is not None
        or live.declarations_hash == profile_declarations_hash(intent.access_profiles)
        or not held_profiles
    ):
        return held_profiles
    raise LocalStateError(t("profiles.review_required"), hint=t("profiles.review_required_hint"))


def profile_review_needed(cluster: ClusterConfig, intent: SetupIntent, *, allow_bootstrap: bool = False) -> bool:
    """Named memberships and declaration history require review, including an empty map."""
    if intent.control.backend != "meridian":
        return False
    if intent.access_profiles:
        return True
    if not cluster.is_configured:
        if allow_bootstrap and not cluster.has_used_access_profiles:
            return False
        raise ProfileReviewUnavailableError()
    try:
        with ControlClient(cluster.panel.url, cluster.panel.api_token) as client:
            return _held_profiles(client.access_profiles())
    except ControlServiceError as exc:
        if not _unavailable(exc):
            raise
        if allow_bootstrap and not cluster.has_used_access_profiles:
            return False
        raise ProfileReviewUnavailableError() from exc


def _held_profiles(live: AccessProfileCatalog) -> bool:
    return (
        live.declarations_hash is not None
        or live.has_non_default_memberships
        or any(not profile.built_in for profile in live.profiles)
    )


def _unavailable(exc: ControlServiceError) -> bool:
    return exc.category == "system" or isinstance(exc, ControlServiceNotFound)


def review_profile_access(cluster: ClusterConfig, plan: ResourcePlan) -> AccessProfileReview | None:
    return review_profile_resources(cluster, plan.resources)


def review_profile_resources(
    cluster: ClusterConfig, resources: Sequence[CompiledResource]
) -> AccessProfileReview | None:
    settings = next((row.payload for row in resources if isinstance(row.payload, ServiceSettingsPayload)), None)
    if settings is None:
        return None
    if not cluster.is_configured:
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
