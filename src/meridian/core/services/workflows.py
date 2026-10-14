"""Workflow discovery services for meridian-core clients."""

from __future__ import annotations

from meridian.core.deploy import DeployRequest, build_deploy_workflow
from meridian.core.servers import build_server_onboarding_workflow
from meridian.core.workflow import WorkflowCatalogEntry, WorkflowPlan


class WorkflowNotFoundError(ValueError):
    """Raised when a requested workflow is not registered."""


def collect_workflow(name: str) -> WorkflowPlan:
    """Return a UI-renderable workflow plan by name."""
    if name == "server-onboarding":
        return build_server_onboarding_workflow()
    if name == "deploy":
        return build_deploy_workflow(DeployRequest())
    raise WorkflowNotFoundError(f"Unknown workflow {name!r}")


def workflow_catalog() -> list[WorkflowCatalogEntry]:
    """Return discoverable UI-renderable workflows."""
    workflows = [collect_workflow("server-onboarding"), collect_workflow("deploy")]
    return [
        WorkflowCatalogEntry(
            id=workflow.id,
            title=workflow.title,
            summary=workflow.summary,
            ready_request_schema=workflow.ready_request_schema,
            stability="preview",
        )
        for workflow in workflows
    ]
