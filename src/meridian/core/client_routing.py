"""Request and result contracts for the reviewed client routing choice."""

from __future__ import annotations

from pydantic import Field

from meridian.core.apply import CompiledApplyPreview, CompiledApplyResult
from meridian.core.delivery_policies import ClientRoutingPolicy, DeliveryPolicySettings
from meridian.core.inputs import TopologyIdValue
from meridian.core.models import CoreModel


class FleetRoutingRequest(CoreModel):
    client_routing: TopologyIdValue | None = None
    dry_run: bool = False
    yes: bool = False


class ClientRoutingChoice(CoreModel):
    policy: TopologyIdValue
    description: str
    preset: ClientRoutingPolicy | None = Field(default=None, exclude_if=lambda value: value is None)
    rules: int | None = Field(default=None, ge=0, le=128, exclude_if=lambda value: value is None)


def client_routing_choices(settings: DeliveryPolicySettings | None = None) -> list[ClientRoutingChoice]:
    choices = [
        ClientRoutingChoice(
            policy="ru-split",
            description="Send Russian destinations and private/LAN ranges directly; tunnel other traffic.",
        ),
        ClientRoutingChoice(
            policy="full",
            description="Tunnel internet traffic, including Russian destinations; bypass private/LAN ranges.",
        ),
    ]
    if settings is not None:
        choices += [
            ClientRoutingChoice(
                policy=policy.id,
                preset=policy.preset,
                rules=len(policy.rules),
                description=f"Custom policy based on {policy.preset}, with {len(policy.rules)} ordered rules.",
            )
            for policy in settings.delivery_policies
        ]
    return choices


class FleetRoutingResult(CoreModel):
    """Current or reviewed client policy, its meaning, and the apply when it ran."""

    client_routing: TopologyIdValue
    choices: list[ClientRoutingChoice] = Field(default_factory=client_routing_choices, max_length=66)
    notes: list[str] = Field(
        default_factory=lambda: [
            "Both policies block QUIC (UDP/443) after direct destination rules so apps fall back to TCP.",
            "Both retain Xray JSON's direct rule for detected BitTorrent peers to reduce abuse complaints "
            "against exits; encrypted peers and UDP DHT/uTP may still use the tunnel.",
            "Base64 links carry no routing rules; configure routing in those apps separately.",
        ]
    )
    dry_run: bool = False
    review: CompiledApplyPreview | None = None
    applied: CompiledApplyResult | None = None
