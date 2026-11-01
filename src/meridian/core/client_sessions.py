"""Credential generations and per-node evidence that new authenticated flows are blocked."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from meridian.core.agent_types import MAX_SAFE_INTEGER, InboundTag, NodeId, NodeStateRef
from meridian.core.models import CoreModel

CredentialGeneration = Annotated[int, Field(strict=True, ge=1, le=MAX_SAFE_INTEGER)]
FlowBlockStatus = Literal["blocked", "pending_node", "agent_too_old"]
FlowBlockReason = Literal["disabled", "expired", "limited", "removed", "credentials", "access_changed"]


class UserCutOffRequest(CoreModel):
    cut_off: bool = False


class CredentialRotationRequest(UserCutOffRequest):
    expected_generation: CredentialGeneration = Field(
        description="Read credential_generation from the person first; stale requests cannot rotate again."
    )


class UserFlowBlockNode(CoreModel):
    node_id: NodeId
    credential_generation: CredentialGeneration
    reason: FlowBlockReason
    status: FlowBlockStatus
    required: NodeStateRef = Field(
        description="The revocation revision; fresh applied revisions at or after it in the same epoch also cover it."
    )
    inbound_tags: list[InboundTag] = Field(default_factory=list, max_length=64)


class UserFlowBlocks(CoreModel):
    """A blocked result covers new streams and UDP associations, not already dispatched flows."""

    username: str
    credential_generation: CredentialGeneration
    nodes: list[UserFlowBlockNode] = Field(
        default_factory=list,
        description="One receipt per revoked credential generation and node; fingerprints stay private.",
    )
