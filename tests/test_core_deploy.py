"""Tests for meridian-core deploy request and workflow contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from meridian.core.deploy import DeployRequest, build_deploy_workflow
from meridian.core.validation import validation_error_hint


def test_deploy_workflow_describes_wizard_inputs() -> None:
    workflow = build_deploy_workflow(DeployRequest())

    assert workflow.needs_input is True
    assert workflow.ready_request_schema == "deploy-request"
    assert [section.id for section in workflow.sections] == ["target", "stealth", "experience", "advanced"]
    assert [field.id for field in workflow.fields] == [
        "ip",
        "user",
        "harden",
        "sni",
        "domain",
        "server_name",
        "icon",
        "color",
        "client_name",
        "warp",
        "geo_block",
        "confirm",
    ]
    color_field = next(field for field in workflow.fields if field.id == "color")
    assert color_field.kind == "choice"
    assert [option.value for option in color_field.options] == [
        "ocean",
        "sunset",
        "forest",
        "lavender",
        "rose",
        "slate",
    ]


def test_deploy_workflow_is_ready_when_target_is_supplied() -> None:
    assert build_deploy_workflow(DeployRequest(ip="198.51.100.10")).needs_input is False
    assert build_deploy_workflow(DeployRequest(requested_server="edge")).needs_input is False


def test_deploy_request_validates_human_readable_input_errors() -> None:
    with pytest.raises(ValidationError) as exc_info:
        DeployRequest(ip="not-an-ip", user="bad user", client_name="bad name!")

    hint = validation_error_hint(exc_info.value)
    assert "ip: Enter a valid IP address, or use 'local' when running Meridian on the target server." in hint
    assert "user: Use letters, numbers, dots, hyphens, and underscores." in hint
    assert "client_name: Use letters, numbers, hyphens, and underscores." in hint
