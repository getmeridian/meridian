"""Secret-free deployment contract and intent normalization helpers."""

from __future__ import annotations

from typing import Any

from meridian.compiler.errors import TopologyCompileError
from meridian.compiler.models import DeploymentContract
from meridian.core.topology import SetupIntent


def normalized_intent(intent: SetupIntent) -> dict[str, Any]:
    """Return the canonical order-insensitive intent representation."""
    payload = intent.model_dump(mode="json")
    payload["exits"] = sorted(payload["exits"], key=lambda value: value["id"])
    for exit_ in payload["exits"]:
        exit_["paths"] = sorted(exit_["paths"], key=lambda value: value["id"])
    payload["transparent_relays"] = sorted(payload["transparent_relays"], key=lambda value: value["id"])
    payload["routing_gateways"] = sorted(payload["routing_gateways"], key=lambda value: value["id"])
    payload["egress_pools"] = sorted(payload["egress_pools"], key=lambda value: value["id"])
    payload["routes"] = sorted(payload["routes"], key=lambda value: (value["priority"], value["id"]))
    payload["access"]["users"] = sorted(payload["access"]["users"])
    payload["delivery"]["formats"] = sorted(payload["delivery"]["formats"])
    return payload


def validate_deployment_contract(
    intent: SetupIntent,
    contract: DeploymentContract,
) -> None:
    """Require a populated deployment contract to cover every server exactly."""
    if not contract.server_targets:
        return
    required = deployment_server_refs(intent)

    supplied = set(contract.server_targets)
    if supplied == required:
        return
    details: list[str] = []
    missing = sorted(required - supplied)
    extra = sorted(supplied - required)
    if missing:
        details.append("missing " + ", ".join(missing))
    if extra:
        details.append("unexpected " + ", ".join(extra))
    raise TopologyCompileError("Deployment targets must exactly cover the topology: " + "; ".join(details) + ".")


def deployment_server_refs(intent: SetupIntent) -> set[str]:
    """Return the exact server references that can receive mutations."""
    return {
        intent.control.server_ref,
        *(exit_.server_ref for exit_ in intent.exits),
        *(gateway.server_ref for gateway in intent.routing_gateways),
        *(server_ref for relay in intent.transparent_relays for server_ref in relay.hop_server_refs),
    }
