"""Node restart requests and results compose the existing per-node command contract."""

from __future__ import annotations

from typing import Literal

from meridian.control.command_models import NodeCommandOutcome
from meridian.core.command_inputs import NodeTargetRequest
from meridian.core.models import CoreModel


class NodeRestartRequest(NodeTargetRequest):
    scope: Literal["core", "all"] = "core"
    yes: bool = False


class NodeRestartResult(CoreModel):
    scope: Literal["core", "all"]
    nodes: list[NodeCommandOutcome]
