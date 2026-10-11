"""Resolve runtime-only credentials for reviewed Xray service edges."""

from __future__ import annotations

from typing import TYPE_CHECKING

from meridian.compiler.models import ConfigProfilePayload, ServiceUserPayload
from meridian.reconciler.resources import ResourceReconcileError
from meridian.remnawave.models import is_user_id
from meridian.xray_workload import ServiceRouteCredential

if TYPE_CHECKING:
    from meridian.reconciler.remnawave_drivers import RemnawaveDriverContext


def resolve_service_credentials(
    context: RemnawaveDriverContext,
    payload: ConfigProfilePayload,
    generation: int,
) -> dict[str, ServiceRouteCredential]:
    credentials: dict[str, ServiceRouteCredential] = {}
    for edge in payload.service_outbounds:
        user_id = context.remote_id(edge.service_user_ref, generation)
        if not user_id:
            raise ResourceReconcileError(f"Service user binding {edge.service_user_ref!r} is missing.")
        if is_user_id(user_id):
            user = context.panel.get_user_by_id(user_id)
        else:
            # Pre-3.x bindings hold the removed user uuid; re-resolve by username.
            service_user = context.resource_payload(edge.service_user_ref, ServiceUserPayload)
            user = context.panel.get_user(service_user.username)
        if user is None or not user.vless_uuid:
            raise ResourceReconcileError(f"Service user {edge.service_user_ref!r} has no VLESS credential.")
        target = context.workloads.require(edge.target_workload_ref, generation)
        keys = target.reality_keys.get(edge.target_server_ref)
        if keys is None or not keys.public_key or not keys.short_id:
            raise ResourceReconcileError(
                f"Target workload {edge.target_workload_ref!r} has incomplete Reality client keys."
            )
        credentials[edge.edge_id] = ServiceRouteCredential(
            address=context.address(edge.target_server_ref),
            vless_uuid=user.vless_uuid,
            public_key=keys.public_key,
            short_id=keys.short_id,
        )
    return credentials
