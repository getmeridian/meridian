"""Tests for workflow discovery services."""

from __future__ import annotations

import pytest

from meridian.core.services import WorkflowNotFoundError, collect_workflow


def test_collect_workflow_returns_deploy_plan() -> None:
    workflow = collect_workflow("deploy")

    assert workflow.id == "deploy"
    assert workflow.needs_input is True
    assert workflow.ready_request_schema == "deploy-request"
    assert any(field.id == "ip" for field in workflow.fields)


def test_collect_workflow_rejects_unknown_name() -> None:
    with pytest.raises(WorkflowNotFoundError, match="Unknown workflow"):
        collect_workflow("missing")
