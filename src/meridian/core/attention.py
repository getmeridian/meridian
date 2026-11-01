"""Pure attention predicates over existing aggregate user and node evidence."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal, Self

from pydantic import Field, model_validator

from meridian.control.models import NodeStatus, UserRecord
from meridian.core.agent_types import NodeId, Timestamp
from meridian.core.models import CoreModel
from meridian.core.server_allowances import ServerUsage

MAX_EXPIRY_DAYS = 30
MAX_UNUSED_DAYS = 365
MAX_STOPPED_DAYS = MAX_UNUSED_DAYS
MAX_PAYMENT_DAYS = 30
PersonAttentionReason = Literal["expiring", "quota", "unused", "stopped"]
NodeAttentionReason = Literal["offline", "apply_failed", "unhealthy"]
ServerAttentionReason = Literal["allowance_near", "allowance_exceeded", "payment_due", "payment_overdue"]


class AttentionThresholds(CoreModel):
    expiry_days: int = Field(default=7, ge=1, le=MAX_EXPIRY_DAYS)
    quota_percent: int = Field(default=80, ge=1, le=100)
    unused_days: int = Field(default=3, ge=1, le=MAX_UNUSED_DAYS)
    stopped_days: int = Field(default=3, ge=1, le=MAX_STOPPED_DAYS)
    allowance_percent: int = Field(default=80, ge=1, le=100)
    allowance_exceeded_percent: int = Field(default=100, ge=1, le=100)
    payment_days: int = Field(default=7, ge=1, le=MAX_PAYMENT_DAYS)

    @model_validator(mode="after")
    def ordered_allowance_thresholds(self) -> Self:
        if self.allowance_percent > self.allowance_exceeded_percent:
            raise ValueError("allowance_percent must not exceed allowance_exceeded_percent.")
        return self


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


class ServerAttention(CoreModel):
    server_ref: str
    reasons: list[ServerAttentionReason] = Field(min_length=1, max_length=2)
    usage: ServerUsage


class AttentionList(CoreModel):
    """Read-only warnings derived at observed_at; no attention state is stored."""

    observed_at: Timestamp
    thresholds: AttentionThresholds
    people: list[PersonAttention]
    nodes: list[NodeAttention]
    servers: list[ServerAttention] = Field(default_factory=list)


def is_never_used(user: UserRecord) -> bool:
    """No known lifetime traffic or aggregate activity, including undated imported use."""
    return user.first_traffic_at is None and user.online_at is None and user.lifetime_traffic_bytes == 0


def person_reasons(user: UserRecord, thresholds: AttentionThresholds, moment: datetime) -> list[PersonAttentionReason]:
    """Disjoint never-used and stopped-use findings from default aggregate evidence."""
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
    if is_never_used(user) and moment - datetime.fromisoformat(user.created_at) > timedelta(
        days=thresholds.unused_days
    ):
        reasons.append("unused")
    if user.online_at is not None and moment - datetime.fromisoformat(user.online_at) > timedelta(
        days=thresholds.stopped_days
    ):
        reasons.append("stopped")
    return reasons


def derive_attention(
    users: Sequence[UserRecord],
    nodes: Sequence[NodeStatus],
    thresholds: AttentionThresholds,
    now: str,
    *,
    unhealthy_nodes: Collection[str] = (),
    server_usage: Sequence[ServerUsage] = (),
) -> AttentionList:
    moment = datetime.fromisoformat(now)
    people: list[PersonAttention] = []
    for user in users:
        reasons = person_reasons(user, thresholds, moment)
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
    return AttentionList(
        observed_at=now,
        thresholds=thresholds,
        people=people,
        nodes=attention_nodes,
        servers=derive_server_attention(server_usage, thresholds, now),
    )


def derive_server_attention(
    servers: Sequence[ServerUsage], thresholds: AttentionThresholds, now: str
) -> list[ServerAttention]:
    today = datetime.fromisoformat(now).astimezone(UTC).date()
    result: list[ServerAttention] = []
    for server in servers:
        reasons: list[ServerAttentionReason] = []
        limit, used = server.metadata.allowance_bytes, server.used_bytes
        if limit is not None and used is not None:
            if used * 100 >= limit * thresholds.allowance_exceeded_percent:
                reasons.append("allowance_exceeded")
            elif used * 100 >= limit * thresholds.allowance_percent:
                reasons.append("allowance_near")
        due = server.next_payment_on
        if due is not None:
            if due < today:
                reasons.append("payment_overdue")
            elif due <= today + timedelta(days=thresholds.payment_days):
                reasons.append("payment_due")
        if reasons:
            result.append(ServerAttention(server_ref=server.server_ref, reasons=reasons, usage=server))
    return result
