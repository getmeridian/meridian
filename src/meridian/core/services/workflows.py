"""Workflow plans for meridian-core clients."""

from __future__ import annotations

from meridian.core.deploy import DeployRequest, build_deploy_workflow
from meridian.core.workflow import WorkflowPlan


class WorkflowNotFoundError(ValueError):
    """Raised when a requested workflow is not registered."""


def collect_workflow(name: str) -> WorkflowPlan:
    """Return a workflow plan by name."""
    if name == "deploy":
        return build_deploy_workflow(DeployRequest())
    raise WorkflowNotFoundError(f"Unknown workflow {name!r}")
