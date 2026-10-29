"""Project stored agent runtime evidence without adding report parsing to the service."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from meridian.control.models import NodeRuntime


def project_runtime(report: dict[str, Any]) -> NodeRuntime | None:
    """Old or incomplete cached reports remain unavailable evidence, rather than crashing reads."""
    try:
        return NodeRuntime.model_validate({key: report.get(key) for key in ("health", "versions", "observed_at")})
    except ValidationError:
        return None
