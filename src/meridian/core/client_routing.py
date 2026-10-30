"""Request and result contracts for the reviewed client routing choice."""

from __future__ import annotations

from pydantic import Field

from meridian.core.apply import CompiledApplyPreview, CompiledApplyResult
from meridian.core.models import CoreModel
from meridian.core.topology import ClientRoutingPolicy


class FleetRoutingRequest(CoreModel):
    client_routing: ClientRoutingPolicy | None = None
    dry_run: bool = False
    yes: bool = False


class ClientRoutingChoice(CoreModel):
    policy: ClientRoutingPolicy
    description: str


def _choices() -> list[ClientRoutingChoice]:
    return [
        ClientRoutingChoice(
            policy="ru-split",
            description="Send Russian destinations and private/LAN ranges directly; tunnel other traffic.",
        ),
        ClientRoutingChoice(
            policy="full",
            description="Tunnel internet traffic, including Russian destinations; bypass private/LAN ranges.",
        ),
    ]


class FleetRoutingResult(CoreModel):
    """Current or reviewed client policy, its meaning, and the apply when it ran."""

    client_routing: ClientRoutingPolicy
    choices: list[ClientRoutingChoice] = Field(default_factory=_choices)
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
