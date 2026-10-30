"""Schema registration for reviewed fleet delivery commands."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from meridian.core.client_routing import FleetRoutingRequest, FleetRoutingResult
from meridian.core.schema_envelopes import make_command_envelope


def delivery_schemas(
    *, envelope_base: type[BaseModel], failure_data_type: type[BaseModel]
) -> dict[str, type[BaseModel]]:
    """Use the catalog's shared envelope models, preserving command schema names and fields."""
    _, _, routing_envelope = make_command_envelope(
        "FleetRouting",
        "fleet.routing",
        FleetRoutingResult,
        envelope_base=envelope_base,
        failure_data_type=failure_data_type,
        success_statuses=Literal["ok", "changed", "no_changes"],
        doc="Envelope schema for `meridian fleet routing --json`.",
    )
    return {
        "fleet-routing-envelope": routing_envelope,
        "fleet-routing": FleetRoutingResult,
        "fleet-routing-request": FleetRoutingRequest,
    }
