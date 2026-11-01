"""Secret-free node command requests, records, and targets shared by the API and deployer."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, RootModel

from meridian.control.models import UserRecord
from meridian.core.agent_protocol import AgentCommand, Capability, CommandEffect, CommandResult, CommandResultStatus
from meridian.core.agent_types import InboundTag, NodeId, NodeStateRef, ResourceId
from meridian.core.client_sessions import UserCutOffRequest as UserCutOffRequest
from meridian.core.models import CoreModel


class CommandCreate(CoreModel):
    expires_in_seconds: int = Field(default=300, ge=30, le=3600)
    wait_for: NodeStateRef | None = Field(
        default=None,
        description="Deliver only after this node reports this revision applied (or a newer one in the same epoch).",
    )


class ReconcileCreate(CommandCreate):
    kind: Literal["reconcile"] = "reconcile"


class ReportCreate(CommandCreate):
    kind: Literal["report"] = "report"
    include_stats: bool


class ResetInboundCreate(CommandCreate):
    kind: Literal["reset_inbound"] = "reset_inbound"
    inbound_tag: InboundTag


class RestartUnitCreate(CommandCreate):
    kind: Literal["restart_unit"] = "restart_unit"
    resource_id: ResourceId


class RenewCredentialCreate(CommandCreate):
    kind: Literal["renew_credential"] = "renew_credential"


CommandRequest = Annotated[
    ReconcileCreate | ReportCreate | ResetInboundCreate | RestartUnitCreate | RenewCredentialCreate,
    Field(discriminator="kind"),
]


class NodeCommandRequest(RootModel[CommandRequest]):
    """The admin body names a command kind and only that kind's fields."""


class NodeCommandRecord(CoreModel):
    node_id: NodeId
    command: AgentCommand
    status: Literal["waiting", "pending", CommandResultStatus]
    wait_for: NodeStateRef | None
    result: CommandResult | None


class CommandUnitTarget(CoreModel):
    resource_id: ResourceId
    name: str
    core: bool


class NodeCommandTargets(CoreModel):
    node_id: NodeId
    units: list[CommandUnitTarget]
    inbounds: list[InboundTag]
    capabilities: list[Capability]


class CutOffNode(CoreModel):
    node_id: NodeId
    inbound_tags: list[InboundTag]
    effect: CommandEffect = Field(
        default="inbound_reset",
        description="A core restart reconnects every Xray inbound and bridge on this node; "
        "an inbound reset reconnects the listed inbounds, with whole-core fallback on failure.",
    )


class CutOffPlan(CoreModel):
    """Every managed inbound that may hold the person's open sessions; contains no person or credential."""

    nodes: list[CutOffNode]


class UserCutOffMutation(CoreModel):
    user: UserRecord | None
    commands: list[NodeCommandRecord]


NodeCommandOutcomeStatus = Literal["done", "core_restart", "failed", "timed_out"]


class NodeCommandOutcome(CoreModel):
    """One node's terminal result, preserving every inbound/unit command and any partial success."""

    node_id: NodeId
    status: NodeCommandOutcomeStatus
    commands: list[NodeCommandRecord] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
