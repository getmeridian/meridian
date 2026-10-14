"""Delete Remnawave objects whose resources left the reviewed plan.

Each mixin is idempotent: an empty remote ID (never created) or a 404 counts
as already retired. Drivers inherit these and keep a ``context`` exposing
``panel``.
"""

from __future__ import annotations

import time
from typing import Any

from meridian.cluster import ManagedResourceBinding
from meridian.compiler.names import service_username
from meridian.compiler.routing import edge_id
from meridian.reconciler.resources import ResourceReconcileError
from meridian.remnawave import RemnawaveNotFoundError
from meridian.remnawave.control_plane import SERVICE_USER_DESCRIPTION
from meridian.remnawave.models import is_user_id

# Remnawave deletes Nodes from a background queue after answering DELETE.
NODE_DELETE_TIMEOUT_SECONDS = 60.0
NODE_DELETE_POLL_SECONDS = 2.0


class HostRetirement:
    """Delete a Host whose path left the plan."""

    context: Any

    def retire(self, binding: ManagedResourceBinding) -> None:
        if not binding.remote_id:
            return
        try:
            self.context.panel.delete_host(binding.remote_id)
        except RemnawaveNotFoundError:
            pass


class NodeRetirement:
    """Deregister a panel Node and wait until the panel has really removed it."""

    context: Any

    def retire(self, binding: ManagedResourceBinding) -> None:
        if not binding.remote_id:
            return
        panel = self.context.panel
        try:
            panel.delete_node(binding.remote_id)
        except RemnawaveNotFoundError:
            return
        deadline = time.monotonic() + NODE_DELETE_TIMEOUT_SECONDS
        while panel.get_node(binding.remote_id) is not None:
            if time.monotonic() >= deadline:
                raise ResourceReconcileError(
                    f"Remnawave accepted the deletion of Node {binding.remote_id} but has not removed it yet.",
                    hint="Wait a minute, then rerun `meridian apply` to finish the removal.",
                    retryable=True,
                )
            time.sleep(NODE_DELETE_POLL_SECONDS)


class ConfigProfileRetirement:
    """Delete a config Profile (and the Inbounds inside it) after its Nodes are gone."""

    context: Any

    def retire(self, binding: ManagedResourceBinding) -> None:
        if binding.remote_id:
            self.context.panel.delete_config_profile(binding.remote_id)


class InternalSquadRetirement:
    context: Any

    def retire(self, binding: ManagedResourceBinding) -> None:
        if binding.remote_id:
            self.context.panel.delete_internal_squad(binding.remote_id)


class ExternalSquadRetirement:
    context: Any

    def retire(self, binding: ManagedResourceBinding) -> None:
        if binding.remote_id:
            self.context.panel.delete_external_squad(binding.remote_id)


class ServiceUserRetirement:
    """Delete a gateway bridge service User; uuid-era bindings re-resolve by username."""

    context: Any

    def retire(self, binding: ManagedResourceBinding) -> None:
        panel = self.context.panel
        if is_user_id(binding.remote_id):
            panel.delete_service_user(binding.remote_id)
            return
        username = _service_username_for(binding.logical_id)
        user = panel.get_user(username)
        if user is None:
            return
        if user.description != SERVICE_USER_DESCRIPTION:
            raise ResourceReconcileError(
                f"User {username!r} is no longer marked as a Meridian service user; refusing to delete it.",
                hint="Delete the User in the panel if it is no longer needed, then rerun `meridian apply`.",
                category="user",
            )
        panel.delete_service_user(user.id)


def _service_username_for(logical_id: str) -> str:
    kind, _, edge = logical_id.partition(":")
    gateway_ref, _, exit_ref = edge.partition(":")
    if kind != "service-user" or not gateway_ref or not exit_ref:
        raise ResourceReconcileError(f"Unexpected service User resource ID {logical_id!r}.")
    return service_username(edge_id(gateway_ref, exit_ref))
