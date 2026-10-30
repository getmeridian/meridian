"""Aggregate traffic evidence shared by accounting, API, CLI, and console readers."""

from __future__ import annotations

from pydantic import Field

from meridian.core.agent_types import NodeId, Timestamp
from meridian.core.models import CoreModel


class UserTrafficEvidence(CoreModel):
    first_traffic_at: Timestamp | None = Field(
        default=None, description="When the service first accepted traffic growth; unknown for historical use."
    )
    last_traffic_node: NodeId | None = Field(
        default=None, description="The node whose latest accepted growth set the user's last traffic time."
    )


class NodeTrafficEvidence(CoreModel):
    traffic_counting_since: Timestamp | None = Field(
        default=None,
        description="Complete durable node accounting starts here. Retained counters from before a migration "
        "may also be included; earlier pruned traffic cannot be reconstructed.",
    )
    active_users: int | None = Field(
        default=None, ge=0, description="People with accepted growth in the last report; null before any report."
    )
    active_report_at: Timestamp | None = Field(default=None, description="The last report's collection window time.")
    active_report_received_at: Timestamp | None = Field(
        default=None, description="When the service accepted that report; duplicates leave it unchanged."
    )

    def traffic_evidence(self) -> NodeTrafficEvidence:
        """This evidence alone, including when called on a richer node status model."""
        return NodeTrafficEvidence(
            traffic_counting_since=self.traffic_counting_since,
            active_users=self.active_users,
            active_report_at=self.active_report_at,
            active_report_received_at=self.active_report_received_at,
        )


class NodeTrafficAccounting(NodeTrafficEvidence):
    traffic_bytes: int = Field(
        default=0,
        ge=0,
        description="Durable accepted upload plus download bytes across counter epochs, including retained backfill.",
    )
