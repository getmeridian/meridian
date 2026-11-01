"""Fresh, negotiated evidence for one node's revocation receipts."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from meridian.control.models import AGENT_SEEN_SECONDS
from meridian.core.agent_protocol import ObservedStateReport
from meridian.core.agent_types import NodeStateRef
from meridian.core.client_sessions import FlowBlockStatus


@dataclass(frozen=True)
class FlowBlockEvidence:
    fleet_epoch: int
    current: NodeStateRef | None
    capabilities: tuple[str, ...] | None
    report: ObservedStateReport | None
    fresh: bool

    @classmethod
    def from_node(
        cls, node: sqlite3.Row, *, fleet_epoch: int, now: float, report: ObservedStateReport | None = None
    ) -> FlowBlockEvidence:
        if report is None and node["report"]:
            try:
                report = ObservedStateReport.model_validate_json(node["report"])
            except ValueError:
                pass
        fresh = False
        if node["report_seen_at"]:
            try:
                age = now - datetime.fromisoformat(node["report_seen_at"]).timestamp()
                fresh = 0 <= age < AGENT_SEEN_SECONDS
            except ValueError:
                pass
        current = (
            NodeStateRef(fleet_epoch=fleet_epoch, revision=node["revision"], sha256=node["doc_sha256"])
            if node["revision"]
            else None
        )
        capabilities = tuple(json.loads(node["agent_capabilities"])) if node["agent_capabilities"] is not None else None
        return cls(fleet_epoch, current, capabilities, report, fresh)

    def status(self, required: NodeStateRef) -> FlowBlockStatus:
        if self.capabilities is not None and "user_flow_blocking" not in self.capabilities:
            return "agent_too_old"
        report = self.report
        if (
            self.capabilities is None
            or not self.fresh
            or report is None
            or report.applied is None
            or report.user_flow_blocking is None
            or report.user_flow_blocking.status != "ready"
            or report.user_flow_blocking.document != report.applied
            or required.fleet_epoch != self.fleet_epoch
            or report.applied.fleet_epoch != required.fleet_epoch
            or report.applied.revision < required.revision
            or self.current is None
            or report.applied.revision > self.current.revision
        ):
            return "pending_node"
        if report.applied.revision == required.revision and report.applied.sha256 != required.sha256:
            return "pending_node"
        if report.applied.revision == self.current.revision and report.applied.sha256 != self.current.sha256:
            return "pending_node"
        return "blocked"
