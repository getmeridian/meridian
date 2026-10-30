"""Compose a node's document: its reviewed spec plus one users resource per managed inbound."""

from __future__ import annotations

from typing import Any

from meridian.control.errors import ServiceError
from meridian.control.models import ManagedInbound, NodeSpec, UserRecord, users_resource_id


def with_users(spec: NodeSpec, users: list[UserRecord]) -> list[dict[str, Any]]:
    """Insert one users resource per managed inbound right after the Xray config."""
    resources = [resource.model_dump(mode="json") for resource in spec.resources]
    configs = [index for index, resource in enumerate(resources) if resource["kind"] == "xray_config"]
    if not spec.managed_inbounds:
        return resources
    if len(configs) != 1:
        raise ServiceError("invalid_request", "Managed inbounds need exactly one xray_config resource.", 400)
    config_id = resources[configs[0]]["id"]
    active = [user for user in users if user.status == "active"]
    inserted = [users_resource(inbound, active, config_id) for inbound in spec.managed_inbounds]
    return [*resources[: configs[0] + 1], *inserted, *resources[configs[0] + 1 :]]


def users_resource(inbound: ManagedInbound, users: list[UserRecord], config_id: str) -> dict[str, Any]:
    if inbound.protocol == "hysteria2":
        return {
            "id": users_resource_id(inbound.tag),
            "depends_on": [config_id],
            "kind": "hysteria2_users",
            "inbound_tag": inbound.tag,
            "users": [{"user_id": user.user_id, "auth": user.uuid} for user in users],
        }
    return {
        "id": users_resource_id(inbound.tag),
        "depends_on": [config_id],
        "kind": "xray_users",
        "inbound_tag": inbound.tag,
        # An imported UUID keeps its case for Hysteria2; the document wants VLESS UUIDs lowercase.
        "users": [{"user_id": user.user_id, "uuid": user.uuid.lower(), "flow": inbound.flow} for user in users],
    }
