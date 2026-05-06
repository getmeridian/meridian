"""Topology capability and routing-policy contracts."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from meridian.core.inputs import (
    CountryCodeValue,
    OptionalCountryCodeValue,
    OptionalServerReferenceValue,
    RequiredNameValue,
    ServerReferenceValue,
)
from meridian.core.models import CoreModel

ServerCapability = Literal["panel", "exit", "relay"]
TrafficScope = Literal["default", "country"]


class TopologyServerCapabilities(CoreModel):
    """Capabilities a saved server can provide to topology and routing plans."""

    server_ref: ServerReferenceValue
    capabilities: list[ServerCapability] = Field(min_length=1)
    region: OptionalCountryCodeValue = ""

    @field_validator("capabilities")
    @classmethod
    def reject_duplicate_capabilities(cls, capabilities: list[ServerCapability]) -> list[ServerCapability]:
        if len(capabilities) != len(dict.fromkeys(capabilities)):
            raise ValueError("List each server capability once.")
        return capabilities


class TrafficRouteRule(CoreModel):
    """One routing rule that maps traffic to an exit, optionally through a relay entry."""

    id: RequiredNameValue
    traffic: TrafficScope = "default"
    country_codes: list[CountryCodeValue] = Field(default_factory=list)
    entry_server_ref: OptionalServerReferenceValue = ""
    exit_server_ref: ServerReferenceValue
    enabled: bool = True

    @model_validator(mode="after")
    def validate_traffic_scope(self) -> Self:
        if self.traffic == "country" and not self.country_codes:
            raise ValueError("Choose at least one country code for a country route.")
        if self.traffic == "default" and self.country_codes:
            raise ValueError("Country codes only apply to country routes.")
        return self


class RoutingPolicyDraft(CoreModel):
    """Draft topology policy where capabilities and routing are separate concepts."""

    servers: list[TopologyServerCapabilities] = Field(default_factory=list)
    routes: list[TrafficRouteRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_route_capabilities(self) -> Self:
        capabilities_by_ref: dict[str, set[ServerCapability]] = {}
        for server in self.servers:
            if server.server_ref in capabilities_by_ref:
                raise ValueError(f"Server {server.server_ref} is listed more than once.")
            capabilities_by_ref[server.server_ref] = set(server.capabilities)

        route_ids: set[str] = set()
        for route in self.routes:
            if route.id in route_ids:
                raise ValueError(f"Route {route.id} is listed more than once.")
            route_ids.add(route.id)
            self._require_capability(route.id, route.exit_server_ref, "exit", capabilities_by_ref)
            if route.entry_server_ref:
                self._require_capability(route.id, route.entry_server_ref, "relay", capabilities_by_ref)
        return self

    @staticmethod
    def _require_capability(
        route_id: str,
        server_ref: str,
        capability: ServerCapability,
        capabilities_by_ref: dict[str, set[ServerCapability]],
    ) -> None:
        capabilities = capabilities_by_ref.get(server_ref)
        if capabilities is None:
            raise ValueError(f"Route {route_id} references {server_ref}, but that server has no capabilities.")
        if capability not in capabilities:
            raise ValueError(f"Route {route_id} needs {server_ref} to have {capability} capability.")
