"""Resumable setup draft contracts."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from meridian.core.inputs import (
    OptionalCountryCodeValue,
    OptionalHostnameValue,
    PortValue,
    ServerReferenceValue,
    TopologyIdValue,
)
from meridian.core.models import CoreModel
from meridian.core.topology import (
    AccessIntent,
    ControlPlaneIntent,
    DeliveryIntent,
    EgressPoolIntent,
    ExitIntent,
    OrderedRouteIntent,
    ProtocolPathIntent,
    RoutingGatewayIntent,
    SetupIntent,
    TransparentRelayIntent,
)

SetupSchema = Literal["meridian.setup/v1"]
SetupStage = Literal["servers", "roles", "paths", "routing", "access", "review", "apply", "verify", "handoff"]
SETUP_STAGE_ORDER: tuple[SetupStage, ...] = (
    "servers",
    "roles",
    "paths",
    "routing",
    "access",
    "review",
    "apply",
    "verify",
    "handoff",
)


class SetupExitRole(CoreModel):
    """One saved server assigned an exit workload ID."""

    id: TopologyIdValue
    server_ref: ServerReferenceValue
    region: OptionalCountryCodeValue = ""
    warp: bool = False


class SetupRelayRole(CoreModel):
    """One ordered set of saved servers assigned a transparent relay path."""

    id: TopologyIdValue
    hop_server_refs: list[ServerReferenceValue] = Field(min_length=1)
    exit_ref: TopologyIdValue
    protocol_path_ref: TopologyIdValue
    listen_port: PortValue = 443
    reality_sni: OptionalHostnameValue = ""

    @field_validator("hop_server_refs")
    @classmethod
    def reject_duplicate_hops(cls, hops: list[str]) -> list[str]:
        if len(hops) != len(set(hops)):
            raise ValueError("A relay chain cannot visit the same saved server twice.")
        return hops


class SetupGatewayRole(CoreModel):
    """One saved server assigned an Xray routing-gateway workload."""

    id: TopologyIdValue
    server_ref: ServerReferenceValue
    bridge_path_ref: TopologyIdValue


class SetupRoleSelection(CoreModel):
    """Role assignments collected before protocol details."""

    control: ControlPlaneIntent
    exits: list[SetupExitRole] = Field(min_length=1)
    transparent_relays: list[SetupRelayRole] = Field(default_factory=list)
    routing_gateways: list[SetupGatewayRole] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_duplicate_ids(self) -> Self:
        ids = [
            *[exit_.id for exit_ in self.exits],
            *[relay.id for relay in self.transparent_relays],
            *[gateway.id for gateway in self.routing_gateways],
        ]
        if len(ids) != len(set(ids)):
            raise ValueError("Each setup role needs a unique workload ID.")
        exit_ids = {exit_.id for exit_ in self.exits}
        for relay in self.transparent_relays:
            if relay.exit_ref not in exit_ids:
                raise ValueError(f"Relay {relay.id} references unknown exit {relay.exit_ref!r}.")
        return self


class SetupExitPaths(CoreModel):
    """Protocol paths selected for one exit role."""

    exit_ref: TopologyIdValue
    paths: list[ProtocolPathIntent] = Field(min_length=1)


class SetupPathSelection(CoreModel):
    """Complete protocol choices for all selected exits."""

    exits: list[SetupExitPaths] = Field(min_length=1)

    @model_validator(mode="after")
    def reject_duplicate_exit_paths(self) -> Self:
        refs = [selection.exit_ref for selection in self.exits]
        if len(refs) != len(set(refs)):
            raise ValueError("Configure protocol paths for each exit once.")
        return self


class SetupRoutingSelection(CoreModel):
    """Default egress, ordered routes, and optional failover pools."""

    default_egress_ref: TopologyIdValue
    egress_pools: list[EgressPoolIntent] = Field(default_factory=list)
    routes: list[OrderedRouteIntent] = Field(default_factory=list)


class SetupAccessSelection(CoreModel):
    """Human access and canonical subscription delivery choices."""

    access: AccessIntent
    delivery: DeliveryIntent = Field(default_factory=DeliveryIntent)


class SetupDraft(CoreModel):
    """Versioned, secret-free progress for the resumable setup journey."""

    schema_version: SetupSchema = Field(default="meridian.setup/v1", alias="schema")
    revision: int = Field(default=0, ge=0)
    current_stage: SetupStage = "servers"
    completed_stages: list[SetupStage] = Field(default_factory=list)
    server_refs: list[ServerReferenceValue] = Field(default_factory=list)
    roles: SetupRoleSelection | None = None
    paths: SetupPathSelection | None = None
    routing: SetupRoutingSelection | None = None
    access: SetupAccessSelection | None = None
    review_hash: str = ""
    applied_plan_hash: str = ""
    verified_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_intent(cls, intent: SetupIntent) -> SetupDraft:
        """Start the resumable journey at review for a complete typed intent."""
        server_refs = list(
            dict.fromkeys(
                [
                    intent.control.server_ref,
                    *[exit_.server_ref for exit_ in intent.exits],
                    *[server_ref for relay in intent.transparent_relays for server_ref in relay.hop_server_refs],
                    *[gateway.server_ref for gateway in intent.routing_gateways],
                ]
            )
        )
        roles = SetupRoleSelection(
            control=intent.control,
            exits=[
                SetupExitRole(
                    id=exit_.id,
                    server_ref=exit_.server_ref,
                    region=exit_.region,
                    warp=exit_.warp,
                )
                for exit_ in intent.exits
            ],
            transparent_relays=[
                SetupRelayRole(
                    id=relay.id,
                    hop_server_refs=relay.hop_server_refs,
                    exit_ref=relay.exit_ref,
                    protocol_path_ref=relay.protocol_path_ref,
                    listen_port=relay.listen_port,
                    reality_sni=relay.reality_sni,
                )
                for relay in intent.transparent_relays
            ],
            routing_gateways=[
                SetupGatewayRole(
                    id=gateway.id,
                    server_ref=gateway.server_ref,
                    bridge_path_ref=gateway.bridge_path_ref,
                )
                for gateway in intent.routing_gateways
            ],
        )
        return cls(
            current_stage="review",
            completed_stages=list(SETUP_STAGE_ORDER[:5]),
            server_refs=server_refs,
            roles=roles,
            paths=SetupPathSelection(
                exits=[
                    SetupExitPaths(
                        exit_ref=exit_.id,
                        paths=exit_.paths,
                    )
                    for exit_ in intent.exits
                ]
            ),
            routing=SetupRoutingSelection(
                default_egress_ref=intent.default_egress_ref,
                egress_pools=intent.egress_pools,
                routes=intent.routes,
            ),
            access=SetupAccessSelection(
                access=intent.access,
                delivery=intent.delivery,
            ),
        )

    @model_validator(mode="after")
    def validate_progress(self) -> Self:
        if len(self.server_refs) != len(set(self.server_refs)):
            raise ValueError("Select each saved server once.")
        expected = list(SETUP_STAGE_ORDER[: len(self.completed_stages)])
        if self.completed_stages != expected:
            raise ValueError("Completed setup stages must form an ordered prefix.")
        current_index = SETUP_STAGE_ORDER.index(self.current_stage)
        completed_count = len(self.completed_stages)
        if completed_count > current_index and not (
            self.current_stage == "handoff" and completed_count == len(SETUP_STAGE_ORDER)
        ):
            raise ValueError("Completed setup stages cannot be ahead of the current stage.")
        return self

    def invalidate_from(self, stage: SetupStage) -> SetupDraft:
        """Return a draft with the edited stage and every dependent stage invalidated."""
        stage_index = SETUP_STAGE_ORDER.index(stage)
        updates: dict[str, object] = {
            "revision": self.revision + 1,
            "current_stage": stage,
            "completed_stages": list(SETUP_STAGE_ORDER[:stage_index]),
        }
        if stage_index <= SETUP_STAGE_ORDER.index("servers"):
            updates["server_refs"] = []
        if stage_index <= SETUP_STAGE_ORDER.index("roles"):
            updates["roles"] = None
        if stage_index <= SETUP_STAGE_ORDER.index("paths"):
            updates["paths"] = None
        if stage_index <= SETUP_STAGE_ORDER.index("routing"):
            updates["routing"] = None
        if stage_index <= SETUP_STAGE_ORDER.index("access"):
            updates["access"] = None
        if stage_index <= SETUP_STAGE_ORDER.index("review"):
            updates["review_hash"] = ""
        if stage_index <= SETUP_STAGE_ORDER.index("apply"):
            updates["applied_plan_hash"] = ""
        if stage_index <= SETUP_STAGE_ORDER.index("verify"):
            updates["verified_at"] = ""
        return self.model_copy(update=updates)

    def complete_current_stage(self) -> SetupDraft:
        """Advance after validating that the current stage has durable data."""
        self._require_current_stage_data()
        current_index = SETUP_STAGE_ORDER.index(self.current_stage)
        completed = list(SETUP_STAGE_ORDER[: current_index + 1])
        next_stage = SETUP_STAGE_ORDER[min(current_index + 1, len(SETUP_STAGE_ORDER) - 1)]
        return self.model_copy(
            update={
                "revision": self.revision + 1,
                "current_stage": next_stage,
                "completed_stages": completed,
            }
        )

    def to_intent(self) -> SetupIntent:
        """Build complete compiler input or reject an incomplete draft."""
        if self.roles is None or self.paths is None or self.routing is None or self.access is None:
            raise ValueError("Setup is incomplete; finish roles, paths, routing, and access before review.")

        selected_servers = set(self.server_refs)
        role_servers = {
            self.roles.control.server_ref,
            *[exit_.server_ref for exit_ in self.roles.exits],
            *[server_ref for relay in self.roles.transparent_relays for server_ref in relay.hop_server_refs],
            *[gateway.server_ref for gateway in self.roles.routing_gateways],
        }
        missing_servers = sorted(role_servers - selected_servers)
        if missing_servers:
            raise ValueError(f"Roles reference servers outside the saved selection: {', '.join(missing_servers)}.")

        paths_by_exit = {selection.exit_ref: selection.paths for selection in self.paths.exits}
        role_exit_ids = {exit_.id for exit_ in self.roles.exits}
        if set(paths_by_exit) != role_exit_ids:
            raise ValueError("Configure exactly one protocol-path set for every selected exit.")

        exits = [
            ExitIntent(
                id=exit_.id,
                server_ref=exit_.server_ref,
                region=exit_.region,
                paths=paths_by_exit[exit_.id],
                warp=exit_.warp,
            )
            for exit_ in self.roles.exits
        ]
        relays = [
            TransparentRelayIntent(
                id=relay.id,
                hop_server_refs=relay.hop_server_refs,
                exit_ref=relay.exit_ref,
                protocol_path_ref=relay.protocol_path_ref,
                listen_port=relay.listen_port,
                reality_sni=relay.reality_sni,
            )
            for relay in self.roles.transparent_relays
        ]
        gateways = [
            RoutingGatewayIntent(
                id=gateway.id,
                server_ref=gateway.server_ref,
                bridge_path_ref=gateway.bridge_path_ref,
            )
            for gateway in self.roles.routing_gateways
        ]
        return SetupIntent(
            control=self.roles.control,
            exits=exits,
            transparent_relays=relays,
            routing_gateways=gateways,
            egress_pools=self.routing.egress_pools,
            routes=self.routing.routes,
            default_egress_ref=self.routing.default_egress_ref,
            access=self.access.access,
            delivery=self.access.delivery,
        )

    def _require_current_stage_data(self) -> None:
        stage = self.current_stage
        if stage == "servers" and not self.server_refs:
            raise ValueError("Select at least one saved server before continuing.")
        if stage == "roles" and self.roles is None:
            raise ValueError("Assign server roles before continuing.")
        if stage == "paths" and self.paths is None:
            raise ValueError("Configure protocol paths before continuing.")
        if stage == "routing" and self.routing is None:
            raise ValueError("Choose routing and failover behavior before continuing.")
        if stage == "access" and self.access is None:
            raise ValueError("Choose access and subscription delivery before continuing.")
        if stage == "review":
            self.to_intent()
            if not self.review_hash:
                raise ValueError("Review the compiled plan before applying it.")
        if stage == "apply" and not self.applied_plan_hash:
            raise ValueError("Apply must finish before verification.")
        if stage == "verify" and not self.verified_at:
            raise ValueError("Live verification must finish before handoff.")
