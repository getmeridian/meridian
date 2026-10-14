"""Results for `meridian route`: per-destination egress overrides on exits."""

from __future__ import annotations

from pydantic import Field

from meridian.core.models import CoreModel
from meridian.core.topology import EgressOverrideIntent, ExitIntent


class ExitEgressOverrides(CoreModel):
    """One exit's default egress and its overrides in first-match order."""

    exit: str
    warp: bool
    overrides: list[EgressOverrideIntent] = Field(default_factory=list)


class RouteListResult(CoreModel):
    """Every exit's egress overrides."""

    exits: list[ExitEgressOverrides] = Field(default_factory=list)


class RouteMutationResult(CoreModel):
    """The override a `route add` created or a `route remove` deleted."""

    exit: str
    override: EgressOverrideIntent


def build_route_list_result(exits: list[ExitIntent]) -> RouteListResult:
    return RouteListResult(
        exits=[
            ExitEgressOverrides(exit=exit_.id, warp=exit_.warp, overrides=list(exit_.egress_overrides))
            for exit_ in exits
        ]
    )
