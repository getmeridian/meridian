"""Capture cut-off scope and queue revision-gated resets in the credential mutation's transaction."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from meridian.control.command_models import (
    CutOffNode,
    CutOffPlan,
    NodeCommandRecord,
    ResetInboundCreate,
    UserCutOffMutation,
)
from meridian.control.commands import CommandLedger
from meridian.control.errors import ServiceError
from meridian.control.models import NodeSpec, UserRecord
from meridian.control.store import Store
from meridian.core.agent_types import NodeStateRef


@dataclass
class PendingCutOff:
    plan: CutOffPlan
    commands: list[NodeCommandRecord] = field(default_factory=list)
    finished: bool = False


class CutOffLedger:
    def __init__(self, store: Store, commands: CommandLedger) -> None:
        self._store = store
        self._commands = commands
        self._local = threading.local()

    def scope(self, username: str) -> CutOffPlan:
        with self._store.lock:
            user = self._store.row("SELECT user_id, uuid, status FROM users WHERE username = ?", (username,))
            if user is None:
                raise ServiceError("not_found", "Unknown user.", 404)
            nodes: list[CutOffNode] = []
            for row in self._store.rows(
                "SELECT node_id, spec, document FROM nodes WHERE spec IS NOT NULL ORDER BY node_id"
            ):
                spec = NodeSpec.model_validate_json(row["spec"])
                if not spec.managed_inbounds or not row["document"]:
                    continue
                tags = {inbound.tag for inbound in spec.managed_inbounds}
                if user["status"] == "active":
                    # Inspect the signed users resources; bridge clients are not managed people.
                    carried: set[str] = set()
                    for resource in json.loads(row["document"])["resources"]:
                        if resource["kind"] in {"xray_users", "hysteria2_users"} and any(
                            client["user_id"] == user["user_id"] for client in resource["users"]
                        ):
                            carried.add(resource["inbound_tag"])
                    tags &= carried
                # Inactive people can still have sessions from before rmu removed them.
                if tags:
                    nodes.append(CutOffNode(node_id=row["node_id"], inbound_tags=sorted(tags)))
            return CutOffPlan(nodes=nodes)

    def change(self, username: str, mutate: Callable[[str], UserRecord | None]) -> UserCutOffMutation:
        """Hold the shared lock across scope capture and the existing mutation.

        `finish()` runs at the end of composition inside that mutation's transaction;
        a full queue or invalid target rolls credentials and documents back with it.
        Nothing depends on the requesting browser or deployer staying connected.
        """
        with self._store.lock:
            if getattr(self._local, "pending", None) is not None:
                raise ServiceError("conflict", "Another cut-off is already being prepared.", 409)
            pending = PendingCutOff(self.scope(username))
            self._local.pending = pending
            try:
                user = mutate(username)
                if not pending.finished:
                    raise ServiceError("internal", "The credential change did not queue its cut-off.", 500)
                return UserCutOffMutation(user=user, commands=pending.commands)
            finally:
                self._local.pending = None

    def finish(self) -> None:
        """Called after all documents compose, inside the same transaction; no nested transaction."""
        pending: PendingCutOff | None = getattr(self._local, "pending", None)
        if pending is None or pending.finished:
            return
        for node in pending.plan.nodes:
            row = self._commands.require_node(node.node_id)
            wanted = NodeStateRef(
                fleet_epoch=self._store.fleet_epoch, revision=row["revision"], sha256=row["doc_sha256"]
            )
            for tag in node.inbound_tags:
                pending.commands.append(
                    self._commands.enqueue(node.node_id, ResetInboundCreate(inbound_tag=tag, wait_for=wanted))
                )
        pending.finished = True
