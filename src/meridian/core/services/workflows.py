"""Workflow discovery services for meridian-core clients."""

from __future__ import annotations

from meridian.core.deploy import DeployRequest, build_deploy_workflow
from meridian.core.workflow import WorkflowCatalogEntry, WorkflowPlan


class WorkflowNotFoundError(ValueError):
    """Raised when a requested workflow is not registered."""


def collect_workflow(name: str) -> WorkflowPlan:
    """Return a UI-renderable workflow plan by name."""
    if name == "deploy":
        return build_deploy_workflow(DeployRequest())
    raise WorkflowNotFoundError(f"Unknown workflow {name!r}")


def workflow_catalog() -> list[WorkflowCatalogEntry]:
    """Return discoverable UI-renderable workflows."""
    deploy = collect_workflow("deploy")
    return [
        WorkflowCatalogEntry(
            id=deploy.id,
            title=deploy.title,
            summary=deploy.summary,
            ready_request_schema=deploy.ready_request_schema,
            stability="preview",
        )
    ]
