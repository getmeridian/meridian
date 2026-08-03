"""Remnawave service-user and Profile-derived routing drivers."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from meridian.cluster import ManagedResourceBinding
from meridian.compiler.models import (
    ConfigProfilePayload,
    EgressPoolPayload,
    RouteRulePayload,
    RoutingGatewayPayload,
    ServiceUserPayload,
    canonical_hash,
)
from meridian.reconciler.remnawave_drivers import RemnawaveDriverContext
from meridian.reconciler.resources import (
    ResourceAction,
    ResourceApplyReceipt,
    ResourceObservation,
    ResourceReconcileError,
    UnknownResourceOutcome,
    postcondition_key,
)
from meridian.reconciler.routing_credentials import resolve_service_credentials
from meridian.remnawave import RemnawaveNetworkError, User
from meridian.xray_workload import render_workload_config

ValueT = TypeVar("ValueT")


class ServiceUserDriver:
    def __init__(self, context: RemnawaveDriverContext) -> None:
        self.context = context

    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        payload = _service_user_payload(action)
        user = _bound_or_named_user(self.context, payload, binding)
        if user is None:
            return ResourceObservation(exists=False)
        squad_uuids = _resolved_ids(self.context, [payload.squad_ref], action.generation)
        matches = (
            user.username == payload.username
            and user.status == "ACTIVE"
            and sorted(user.active_internal_squad_uuids) == sorted(squad_uuids)
            and user.description == "Managed by Meridian service routing"
            and bool(user.vless_uuid)
        )
        return _observed(action, user.uuid, matches, _user_projection(user))

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        payload = _service_user_payload(action)
        squad_uuids = _resolved_ids(self.context, [payload.squad_ref], action.generation)
        existing = _bound_or_named_user(self.context, payload, binding)
        if existing is None:
            user = _mutation(
                lambda: self.context.panel.create_service_user(
                    payload.username,
                    squad_uuids=squad_uuids,
                ),
                f"create service User {payload.username}",
            )
        else:
            owned = (
                binding is not None
                or self.context.was_managed(action.resource.logical_id, existing.uuid)
                or existing.description == "Managed by Meridian service routing"
            )
            if not owned:
                raise ResourceReconcileError(
                    f"Unmanaged User collides with service username {payload.username!r}.",
                    hint="Rename the unmanaged User or explicitly recover its Meridian binding.",
                    category="user",
                )
            user = _mutation(
                lambda: self.context.panel.update_service_user(
                    existing.uuid,
                    squad_uuids=squad_uuids,
                ),
                f"update service User {payload.username}",
            )
        return ResourceApplyReceipt(remote_id=user.uuid)


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
        return _observed(
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


def _service_user_payload(action: ResourceAction) -> ServiceUserPayload:
    payload = action.resource.payload
    if not isinstance(payload, ServiceUserPayload):
        raise ResourceReconcileError(f"Service User driver received {payload.kind}.")
    return payload


def _profile_ref(action: ResourceAction) -> str:
    payload = action.resource.payload
    if isinstance(payload, EgressPoolPayload | RouteRulePayload | RoutingGatewayPayload):
        return payload.profile_ref
    raise ResourceReconcileError(f"Routing projection driver cannot observe {payload.kind}.")


def _resolved_ids(
    context: RemnawaveDriverContext,
    logical_ids: list[str],
    generation: int,
) -> list[str]:
    resolved = [context.remote_id(logical_id, generation) for logical_id in logical_ids]
    if any(not remote_id for remote_id in resolved):
        raise ResourceReconcileError(f"Remote identities are incomplete for {', '.join(logical_ids)}.")
    return resolved


def _bound_or_named_user(
    context: RemnawaveDriverContext,
    payload: ServiceUserPayload,
    binding: ManagedResourceBinding | None,
) -> User | None:
    if binding is not None and binding.remote_id:
        return context.panel.get_user_by_uuid(binding.remote_id)
    return context.panel.get_user(payload.username)


def _observed(
    action: ResourceAction,
    remote_id: str,
    matches: bool,
    projection: object,
) -> ResourceObservation:
    satisfied = [
        postcondition_key(condition.kind, condition.target_ref, condition.detail)
        for condition in action.resource.postconditions
        if condition.kind == "exists"
    ]
    return ResourceObservation(
        exists=True,
        observed_hash=action.expected_hash if matches else canonical_hash(projection),
        remote_id=remote_id,
        satisfied_postconditions=sorted(satisfied),
    )


def _user_projection(user: User) -> dict[str, object]:
    return {
        "username": user.username,
        "status": user.status,
        "squads": sorted(user.active_internal_squad_uuids),
        "description": user.description,
        "has_vless_uuid": bool(user.vless_uuid),
    }


def _mutation(call: Callable[[], ValueT], description: str) -> ValueT:
    try:
        return call()
    except RemnawaveNetworkError as exc:
        raise UnknownResourceOutcome(f"Remnawave may have completed {description}; observation is required.") from exc
