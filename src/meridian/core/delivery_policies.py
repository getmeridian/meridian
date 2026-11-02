"""Reviewed named delivery policies and one resolution rule for every surface."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Literal, Self

from pydantic import Field, model_validator

from meridian.core.defaults import DEFAULT_CLIENT_ROUTING
from meridian.core.delivery_matchers import DeliveryDns, DeliveryMatch, DeliveryNetwork, DeliveryPortRange
from meridian.core.inputs import TopologyIdValue
from meridian.core.models import CoreModel

ClientRoutingPolicy = Literal["ru-split", "full"]
BUILTIN_DELIVERY_POLICIES: tuple[ClientRoutingPolicy, ...] = ("ru-split", "full")
MAX_DELIVERY_POLICY_BYTES = 65_536
MAX_DELIVERY_CATALOG_BYTES = 262_144


class DeliveryPolicyResolutionError(ValueError):
    def __init__(self, code: Literal["unknown_delivery_policy", "conflicting_delivery_policies"], message: str) -> None:
        self.code = code
        super().__init__(message)


class DeliveryAction(CoreModel):
    kind: Literal["proxy", "direct", "block", "exit"]
    exit: TopologyIdValue | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def exit_choice(self) -> Self:
        if (self.kind == "exit") != (self.exit is not None):
            raise ValueError("An exit action needs an exit name; proxy, direct, and block must not name one.")
        return self


class DeliveryRule(CoreModel):
    """Values are alternatives; network and ports narrow the whole match with AND."""

    match: DeliveryMatch
    action: DeliveryAction
    network: DeliveryNetwork | None = Field(default=None, exclude_if=lambda value: value is None)
    ports: list[DeliveryPortRange] = Field(default_factory=list, max_length=16, exclude_if=lambda value: not value)

    @model_validator(mode="after")
    def unique(self) -> Self:
        values = [value.model_dump_json() if isinstance(value, CoreModel) else value for value in self.match.values]
        if len(values) != len(set(values)):
            raise ValueError("List each rule value once.")
        if self.match.kind == "network" and self.network is not None:
            raise ValueError("Put the network in the network match, without a second network condition.")
        if self.match.kind == "port" and self.ports:
            raise ValueError("Put the ports in the port match, without a second port condition.")
        return self


class DeliveryPolicy(CoreModel):
    """LAN always precedes custom rules; the preset's remaining rules follow them."""

    id: TopologyIdValue
    preset: ClientRoutingPolicy = "full"
    rules: list[DeliveryRule] = Field(default_factory=list, max_length=128)
    dns: DeliveryDns | None = None

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if len(self.model_dump_json().encode()) > MAX_DELIVERY_POLICY_BYTES:
            raise ValueError("A delivery policy must fit within 64 KiB. Use fewer rules or values.")
        return self


class DeliveryPolicySettings(CoreModel):
    client_routing: ClientRoutingPolicy = Field(
        default=DEFAULT_CLIENT_ROUTING, exclude_if=lambda value: value == DEFAULT_CLIENT_ROUTING
    )
    delivery_policies: list[DeliveryPolicy] = Field(
        default_factory=list, max_length=64, exclude_if=lambda value: not value
    )
    default_policy: TopologyIdValue | None = Field(default=None, exclude_if=lambda value: value is None)
    profile_policies: dict[TopologyIdValue, TopologyIdValue] = Field(
        default_factory=dict, max_length=64, exclude_if=lambda value: not value
    )

    @model_validator(mode="after")
    def references(self) -> Self:
        names = [policy.id for policy in self.delivery_policies]
        if len(names) != len(set(names)):
            raise ValueError("Delivery policy names must be unique.")
        if set(names) & set(BUILTIN_DELIVERY_POLICIES):
            raise ValueError("The built-in ru-split and full policies cannot be edited or replaced.")
        known = {*names, *BUILTIN_DELIVERY_POLICIES}
        requested = {*self.profile_policies.values()}
        if self.default_policy is not None:
            requested.add(self.default_policy)
        missing = sorted(requested - known)
        if missing:
            raise ValueError("Unknown delivery policies: " + ", ".join(missing) + ".")
        size = len(json.dumps([policy.to_data() for policy in self.delivery_policies], ensure_ascii=False).encode())
        if size > MAX_DELIVERY_CATALOG_BYTES:
            raise ValueError("The delivery policy catalog must fit within 256 KiB.")
        return self


class ResolvedDeliveryPolicy(CoreModel):
    policy: DeliveryPolicy
    built_in: bool
    source: Literal["fleet", "profile", "person"]
    profiles: list[TopologyIdValue] = Field(default_factory=list, max_length=64)


def delivery_policy_catalog(settings: DeliveryPolicySettings) -> list[DeliveryPolicy]:
    return [*(DeliveryPolicy(id=name, preset=name) for name in BUILTIN_DELIVERY_POLICIES), *settings.delivery_policies]


def resolve_delivery_policy(
    settings: DeliveryPolicySettings,
    *,
    profiles: Sequence[str] = (),
    person_override: str | None = None,
) -> ResolvedDeliveryPolicy:
    """Person wins; agreeing profile defaults follow; then the fleet's named policy or preset."""
    source: Literal["fleet", "profile", "person"] = "fleet"
    chosen_profiles: list[str] = []
    name = settings.default_policy or settings.client_routing
    if person_override is not None:
        name, source = person_override, "person"
    else:
        chosen_profiles = sorted({profile for profile in profiles if profile in settings.profile_policies})
        choices = {settings.profile_policies[profile] for profile in chosen_profiles}
        if len(choices) > 1:
            raise DeliveryPolicyResolutionError(
                "conflicting_delivery_policies",
                "These access profiles select different delivery policies. "
                "Set a person override or use agreeing profile defaults.",
            )
        if choices:
            name, source = choices.pop(), "profile"
    policy = next((policy for policy in delivery_policy_catalog(settings) if policy.id == name), None)
    if policy is None:
        raise DeliveryPolicyResolutionError(
            "unknown_delivery_policy",
            f"Unknown delivery policy {name!r}. Choose a policy in the reviewed fleet intent.",
        )
    return ResolvedDeliveryPolicy(
        policy=policy, built_in=name in BUILTIN_DELIVERY_POLICIES, source=source, profiles=chosen_profiles
    )


def delivery_exit_refs(settings: DeliveryPolicySettings) -> set[str]:
    return {
        rule.action.exit
        for policy in settings.delivery_policies
        for rule in policy.rules
        if rule.action.exit is not None
    }
