"""Pure attention predicates over existing aggregate user and node evidence."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import datetime, timedelta
from typing import Literal

from pydantic import Field

from meridian.control.models import NodeStatus, UserRecord
from meridian.core.agent_types import NodeId, Timestamp
from meridian.core.models import CoreModel

MAX_EXPIRY_DAYS = 30
MAX_UNUSED_DAYS = 365
PersonAttentionReason = Literal["expiring", "quota", "unused"]
NodeAttentionReason = Literal["offline", "apply_failed", "unhealthy"]


class AttentionThresholds(CoreModel):
    expiry_days: int = Field(default=7, ge=1, le=MAX_EXPIRY_DAYS)
    quota_percent: int = Field(default=80, ge=1, le=100)
    unused_days: int = Field(default=3, ge=1, le=MAX_UNUSED_DAYS)


class PersonAttention(CoreModel):
    username: str
    reasons: list[PersonAttentionReason] = Field(min_length=1, max_length=3)


class NodeAttention(CoreModel):
    node_id: NodeId
    reasons: list[NodeAttentionReason] = Field(
        min_length=1,
        max_length=3,
        description="unhealthy includes degraded health or non-passing checks from a fresh report.",
    )


class AttentionList(CoreModel):
    """Read-only warnings derived at observed_at; no attention state is stored."""

    observed_at: Timestamp
    thresholds: AttentionThresholds
    people: list[PersonAttention]
    nodes: list[NodeAttention]


def derive_attention(
    users: Sequence[UserRecord],
    nodes: Sequence[NodeStatus],
    thresholds: AttentionThresholds,
    now: str,
    *,
    unhealthy_nodes: Collection[str] = (),
) -> AttentionList:
    moment = datetime.fromisoformat(now)
    people: list[PersonAttention] = []
    for user in users:
        reasons: list[PersonAttentionReason] = []
        if user.expire_at is not None and moment <= datetime.fromisoformat(user.expire_at) <= moment + timedelta(
            days=thresholds.expiry_days
        ):
            reasons.append("expiring")
        if (
            user.traffic_limit_bytes > 0
            and user.used_traffic_bytes * 100 >= user.traffic_limit_bytes * thresholds.quota_percent
        ):
            reasons.append("quota")
        if (
            user.first_traffic_at is None
            and user.online_at is None
            and user.lifetime_traffic_bytes == 0
            and moment - datetime.fromisoformat(user.created_at) > timedelta(days=thresholds.unused_days)
        ):
            reasons.append("unused")
        if reasons:
            people.append(PersonAttention(username=user.username, reasons=reasons))
    attention_nodes: list[NodeAttention] = []
    for node in nodes:
        findings: list[NodeAttentionReason] = []
        if not node.is_live(moment):
            findings.append("offline")
        if node.failed and node.runtime_available(moment):
            findings.append("apply_failed")
        if node.node_id in unhealthy_nodes:
            findings.append("unhealthy")
        if findings:
            attention_nodes.append(NodeAttention(node_id=node.node_id, reasons=findings))
    return AttentionList(observed_at=now, thresholds=thresholds, people=people, nodes=attention_nodes)
