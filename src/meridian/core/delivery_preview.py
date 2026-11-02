"""Typed effective routes, DNS, and format omissions for operator reviews."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from meridian.core.delivery_capabilities import DeliveryFormatCapabilities, DeliveryPolicyOmission, PolicyOmissionReason
from meridian.core.delivery_matchers import DeliveryDns, DeliveryNetwork, DeliveryPortRange
from meridian.core.delivery_policies import DeliveryAction, ResolvedDeliveryPolicy
from meridian.core.delivery_policy_ir import ClientRoute
from meridian.core.models import CoreModel
from meridian.core.subscription_formats import DocumentFormat

DELIVERY_DOCUMENTS: tuple[DocumentFormat, ...] = ("xray-json", "mihomo", "stash", "singbox", "base64")


class DeliveryRoutePreview(CoreModel):
    number: int = Field(ge=1, le=133)
    origin: Literal["lan", "preset", "rule"]
    source_rule: int | None = Field(default=None, ge=1, le=128)
    action: DeliveryAction
    domains: list[str] = Field(default_factory=list, max_length=64)
    ips: list[str] = Field(default_factory=list, max_length=64)
    networks: list[DeliveryNetwork] = Field(default_factory=list, max_length=2)
    ports: list[DeliveryPortRange] = Field(default_factory=list, max_length=16)
    protocols: list[Literal["bittorrent"]] = Field(default_factory=list, max_length=1)


class FormatDeliveryPolicy(CoreModel):
    format: DocumentFormat
    capabilities: DeliveryFormatCapabilities
    rules: list[DeliveryRoutePreview] = Field(max_length=133)
    final_action: DeliveryAction | None
    dns: DeliveryDns | None
    dns_source: Literal["preset", "custom", "omitted"]
    omitted: list[DeliveryPolicyOmission] = Field(max_length=512)


class DeliveryPolicyPreview(CoreModel):
    effective: ResolvedDeliveryPolicy
    formats: list[FormatDeliveryPolicy] = Field(max_length=5)
    geodata_source: str


class SubscriberFormatCapabilities(CoreModel):
    format: DocumentFormat
    routing_supported: bool
    dns_supported: bool
    omitted: list[PolicyOmissionReason] = Field(max_length=8)


class SubscriberDeliveryCapabilities(CoreModel):
    """Format limits only: no policy names, overrides, profiles, match values, or endpoints."""

    formats: list[SubscriberFormatCapabilities] = Field(max_length=5)


def subscriber_delivery_capabilities(preview: DeliveryPolicyPreview) -> SubscriberDeliveryCapabilities:
    return SubscriberDeliveryCapabilities(
        formats=[
            SubscriberFormatCapabilities(
                format=format.format,
                routing_supported=bool(format.capabilities.matches),
                dns_supported=bool(format.capabilities.dns_transports),
                omitted=sorted({omission.reason for omission in format.omitted}),
            )
            for format in preview.formats
        ]
    )


def route_preview(route: ClientRoute) -> DeliveryRoutePreview:
    ports = []
    for raw in route.port.split(",") if route.port else []:
        values = raw.split("-")
        ports.append(DeliveryPortRange(start=int(values[0]), end=int(values[-1])))
    return DeliveryRoutePreview.model_validate(
        {
            "number": route.number,
            "origin": route.origin,
            "source_rule": route.source_rule,
            "action": DeliveryAction(kind=route.action, exit=route.exit_ref or None),
            "domains": list(route.domains),
            "ips": list(route.ips),
            "networks": route.network.split(",") if route.network else [],
            "ports": ports,
            "protocols": list(route.protocols),
        }
    )
