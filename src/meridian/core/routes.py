"""Results for `meridian route`: per-destination egress overrides on exits."""

from __future__ import annotations

from typing import Any

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

    def to_data(self) -> dict[str, Any]:
        from meridian.core.serde import to_plain

        return to_plain(self)


class RouteMutationResult(CoreModel):
    """The override a `route add` created or a `route remove` deleted."""

    exit: str
    override: EgressOverrideIntent

    def to_data(self) -> dict[str, Any]:
        from meridian.core.serde import to_plain

        return to_plain(self)


def build_route_list_result(exits: list[ExitIntent]) -> RouteListResult:
    return RouteListResult(
        exits=[
            ExitEgressOverrides(exit=exit_.id, warp=exit_.warp, overrides=list(exit_.egress_overrides))
            for exit_ in exits
        ]
    )
