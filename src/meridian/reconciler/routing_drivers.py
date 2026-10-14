"""Remnawave service-user and Profile-derived routing drivers."""

from __future__ import annotations

from meridian.cluster import ManagedResourceBinding
from meridian.compiler.models import (
    ConfigProfilePayload,
    EgressPoolPayload,
    RouteRulePayload,
    RoutingGatewayPayload,
    ServiceUserPayload,
)
from meridian.reconciler.remnawave_drivers import RemnawaveDriverContext
from meridian.reconciler.remnawave_retirement import ServiceUserRetirement
from meridian.reconciler.remnawave_support import bound_or_named_user, mutation, observed, resolved_ids
from meridian.reconciler.resources import (
    ResourceAction,
    ResourceApplyReceipt,
    ResourceObservation,
    ResourceReconcileError,
    payload_of,
)
from meridian.reconciler.routing_credentials import resolve_service_credentials
from meridian.remnawave import User
from meridian.remnawave.control_plane import SERVICE_USER_DESCRIPTION
from meridian.xray_workload import render_workload_config


class ServiceUserDriver(ServiceUserRetirement):
    def __init__(self, context: RemnawaveDriverContext) -> None:
        self.context = context

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        payload = payload_of(action, ServiceUserPayload)
        user = bound_or_named_user(self.context, payload.username, binding)
        if user is None:
            return ResourceObservation(exists=False)
        squad_uuids = resolved_ids(self.context, [payload.squad_ref], action.generation)
        matches = (
            user.username == payload.username
            and user.status == "ACTIVE"
            and sorted(user.active_internal_squad_uuids) == sorted(squad_uuids)
            and user.description == SERVICE_USER_DESCRIPTION
            and bool(user.vless_uuid)
        )
        return observed(action, user.id, matches, _user_projection(user))

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        payload = payload_of(action, ServiceUserPayload)
        squad_uuids = resolved_ids(self.context, [payload.squad_ref], action.generation)
        existing = bound_or_named_user(self.context, payload.username, binding)
        if existing is None:
            user = mutation(
                lambda: self.context.panel.create_service_user(
                    payload.username,
                    squad_uuids=squad_uuids,
                ),
                f"create service User {payload.username}",
            )
        else:
            owned = (
                binding is not None
                or self.context.was_managed(action.resource.logical_id, existing.id)
                or existing.description == SERVICE_USER_DESCRIPTION
            )
            if not owned:
                raise ResourceReconcileError(
                    f"Unmanaged User collides with service username {payload.username!r}.",
                    hint="Rename the unmanaged User or explicitly recover its Meridian binding.",
                    category="user",
                )
            user = mutation(
                lambda: self.context.panel.update_service_user(
                    existing.id,
                    squad_uuids=squad_uuids,
                ),
                f"update service User {payload.username}",
            )
        return ResourceApplyReceipt(remote_id=user.id)


class ProfileProjectionDriver:
    """Observe routing resources materialized atomically by their Profile."""

    def __init__(self, context: RemnawaveDriverContext) -> None:
        self.context = context

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        profile_ref = _profile_ref(action)
        payload = self.context.resource_payload(profile_ref, ConfigProfilePayload)
        profile_uuid = self.context.remote_id(profile_ref, action.generation)
        if not profile_uuid:
            workload = self.context.workloads.require(payload.workload_id, action.generation)
            profile_uuid = workload.config_profile_uuid
        profile = self.context.panel.get_config_profile(profile_uuid) if profile_uuid else None
        if profile is None:
            return ResourceObservation(exists=False)
        workload = self.context.workloads.require(payload.workload_id, action.generation)
        expected = render_workload_config(
            payload,
            reality_keys=workload.reality_keys.get(workload.server_refs[0]),
            service_credentials=resolve_service_credentials(
                self.context,
                payload,
                action.generation,
            ),
        )
        matches = profile.config == expected
        return observed(
            action,
            profile.uuid,
            matches,
            {"name": profile.name, "config": profile.config},
        )

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        raise ResourceReconcileError(
            f"Profile did not materialize reviewed {action.resource.payload.kind} {action.resource.logical_id!r}.",
            hint="Reconcile the owning gateway Profile before retrying its routing projection.",
        )


def _profile_ref(action: ResourceAction) -> str:
    payload = action.resource.payload
    if isinstance(payload, EgressPoolPayload | RouteRulePayload | RoutingGatewayPayload):
        return payload.profile_ref
    raise ResourceReconcileError(f"Routing projection driver cannot observe {payload.kind}.")


def _user_projection(user: User) -> dict[str, object]:
    return {
        "username": user.username,
        "status": user.status,
        "squads": sorted(user.active_internal_squad_uuids),
        "description": user.description,
        "has_vless_uuid": bool(user.vless_uuid),
    }
