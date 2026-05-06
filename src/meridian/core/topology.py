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
TrafficRouteAction = Literal["route", "block"]
RegionalTrafficMode = Literal["block", "regional_exit", "default_exit"]


class TopologyServerShelfItem(CoreModel):
    """Beginner-facing saved-server card for topology assignment."""

    server_ref: ServerReferenceValue
    title: str
    host: str
    ssh_user: str = "root"
    ssh_port: int = 22
    validation_state: Literal["unknown", "validated", "key_ready", "failed"] = "unknown"
    capabilities: list[ServerCapability] = Field(default_factory=list)
    region: OptionalCountryCodeValue = ""
    last_checked_at: str = ""


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
    action: TrafficRouteAction = "route"
    country_codes: list[CountryCodeValue] = Field(default_factory=list)
    entry_server_ref: OptionalServerReferenceValue = ""
    exit_server_ref: OptionalServerReferenceValue = ""
    enabled: bool = True

    @model_validator(mode="after")
    def validate_traffic_scope(self) -> Self:
        if self.traffic == "country" and not self.country_codes:
            raise ValueError("Choose at least one country code for a country route.")
        if self.traffic == "default" and self.country_codes:
            raise ValueError("Country codes only apply to country routes.")
        if self.action == "route" and not self.exit_server_ref:
            raise ValueError("Choose an exit server for this route.")
        if self.action == "block":
            if self.traffic != "country":
                raise ValueError("Only country routes can be blocked.")
            if self.entry_server_ref or self.exit_server_ref:
                raise ValueError("Blocked routes cannot use entry or exit servers.")
        return self


class RegionalTrafficDecision(CoreModel):
    """UX-shaped decision for a country or region before it becomes route rules."""

    id: RequiredNameValue
    country_codes: list[CountryCodeValue] = Field(min_length=1)
    mode: RegionalTrafficMode = "block"
    entry_server_ref: OptionalServerReferenceValue = ""
    exit_server_ref: OptionalServerReferenceValue = ""

    @model_validator(mode="after")
    def validate_mode(self) -> Self:
        if self.mode == "regional_exit" and not self.exit_server_ref:
            raise ValueError("Choose where this regional traffic should exit.")
        if self.mode != "regional_exit" and (self.entry_server_ref or self.exit_server_ref):
            raise ValueError("Only regional-exit routing can choose entry or exit servers.")
        return self

    def to_route_rule(self) -> TrafficRouteRule | None:
        """Convert this UX decision into a route rule when it changes routing."""
        if self.mode == "default_exit":
            return None
        if self.mode == "block":
            return TrafficRouteRule(
                id=self.id,
                traffic="country",
                action="block",
                country_codes=self.country_codes,
            )
        return TrafficRouteRule(
            id=self.id,
            traffic="country",
            action="route",
            country_codes=self.country_codes,
            entry_server_ref=self.entry_server_ref,
            exit_server_ref=self.exit_server_ref,
        )


class TopologyBuilderDraft(CoreModel):
    """Beginner topology draft made of saved server cards and plain decisions."""

    servers: list[TopologyServerCapabilities] = Field(default_factory=list)
    regional_traffic: list[RegionalTrafficDecision] = Field(default_factory=list)

    def to_routing_policy(self) -> RoutingPolicyDraft:
        """Compile beginner decisions into the authoritative routing policy."""
        routes = [rule for decision in self.regional_traffic if (rule := decision.to_route_rule()) is not None]
        return RoutingPolicyDraft(servers=self.servers, routes=routes)


class RouteCard(CoreModel):
    """Plain-language route card for Studio and CLI review screens."""

    id: str
    enabled: bool
    action: TrafficRouteAction
    traffic_label: str
    entry_label: str = ""
    exit_label: str = ""
    sentence: str
    warnings: list[str] = Field(default_factory=list)


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
            if route.action == "block":
                continue
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


def route_cards(policy: RoutingPolicyDraft) -> list[RouteCard]:
    """Render routing rules as beginner-readable cards without exposing YAML."""
    regions_by_ref = {server.server_ref: server.region for server in policy.servers}
    cards: list[RouteCard] = []
    for route in policy.routes:
        traffic_label = _traffic_label(route)
        warnings: list[str] = []
        if route.action == "block":
            sentence = f"{traffic_label} -> Blocked"
            cards.append(
                RouteCard(
                    id=route.id,
                    enabled=route.enabled,
                    action=route.action,
                    traffic_label=traffic_label,
                    sentence=sentence,
                    warnings=warnings,
                )
            )
            continue

        entry = route.entry_server_ref
        exit_ref = route.exit_server_ref
        if route.traffic == "country":
            exit_region = regions_by_ref.get(exit_ref, "")
            for country in route.country_codes:
                if exit_region and exit_region != country:
                    warnings.append(f"{country} traffic exits through {exit_region}, not {country}.")
        if entry and entry == exit_ref:
            sentence = f"{traffic_label} -> {exit_ref} regional exit"
        elif entry:
            sentence = f"{traffic_label} -> {entry} relay -> {exit_ref} exit"
        else:
            sentence = f"{traffic_label} -> {exit_ref} exit"
        cards.append(
            RouteCard(
                id=route.id,
                enabled=route.enabled,
                action=route.action,
                traffic_label=traffic_label,
                entry_label=entry,
                exit_label=exit_ref,
                sentence=sentence,
                warnings=warnings,
            )
        )
    return cards


def _traffic_label(route: TrafficRouteRule) -> str:
    if route.traffic == "default":
        return "Default traffic"
    return f"{', '.join(route.country_codes)} traffic"
