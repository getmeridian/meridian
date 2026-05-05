"""Tests for meridian-core deploy service boundary."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from meridian.core.deploy import (
    DeployRequest,
    DeployResult,
    DeployWorkflowAnswers,
    apply_deploy_workflow_answers,
    build_deploy_workflow,
)
from meridian.core.output import OperationContext
from meridian.core.reporters import CaptureReporter
from meridian.core.services.deploy import deploy_server
from meridian.core.validation import validation_error_hint


def _result() -> DeployResult:
    return DeployResult(
        mode="first_deploy",
        server_ip="198.51.100.10",
        ssh_user="root",
        ssh_port=22,
        domain="",
        sni="www.microsoft.com",
        client_name="default",
        harden=True,
        pq=False,
        warp=False,
        geo_block=True,
        panel_url="https://198.51.100.10/panel/",
        panel_secret_path="secret",
        connection_page_path="page",
        node_count=1,
        relay_count=0,
        summary="Deploy completed for 198.51.100.10",
    )


def test_deploy_server_emits_lifecycle_events() -> None:
    request = DeployRequest(ip="198.51.100.10", yes=True)
    reporter = CaptureReporter()
    operation = OperationContext(operation_id="op-deploy", started_at="2026-05-04T21:00:00Z")
    seen: dict[str, object] = {}

    def executor(req: DeployRequest, rep: object, op: OperationContext) -> DeployResult:
        seen["request"] = req
        seen["reporter"] = rep
        seen["operation"] = op
        return _result()

    result = deploy_server(request, executor=executor, reporter=reporter, operation=operation)

    assert result.server_ip == "198.51.100.10"
    assert seen == {"request": request, "reporter": reporter, "operation": operation}
    assert [event.type for event in reporter.events] == ["command.started", "command.completed"]
    assert {event.operation_id for event in reporter.events} == {"op-deploy"}
    assert reporter.events[-1].data["mode"] == "first_deploy"


def test_deploy_server_emits_failure_event() -> None:
    request = DeployRequest(ip="198.51.100.10", yes=True)
    reporter = CaptureReporter()

    def executor(_req: DeployRequest, _rep: object, _op: OperationContext) -> DeployResult:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        deploy_server(request, executor=executor, reporter=reporter)

    assert [event.type for event in reporter.events] == ["command.started", "command.failed"]
    assert reporter.events[-1].level == "error"
    assert reporter.events[-1].data["cause"] == "RuntimeError"


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
        "pq",
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


def test_apply_deploy_workflow_answers_updates_request() -> None:
    request = DeployRequest(yes=True, ssh_port=2222)
    updated = apply_deploy_workflow_answers(
        request,
        DeployWorkflowAnswers(
            ip="198.51.100.10",
            user="admin",
            sni="www.microsoft.com",
            domain="vpn.example",
            harden=False,
            client_name="alice",
            server_name="Family VPN",
            icon="shield",
            color="ocean",
            pq=True,
            warp=True,
            geo_block=False,
            confirm=True,
        ),
    )

    assert updated.ip == "198.51.100.10"
    assert updated.user == "admin"
    assert updated.domain == "vpn.example"
    assert updated.harden is False
    assert updated.client_name == "alice"
    assert updated.pq is True
    assert updated.warp is True
    assert updated.geo_block is False
    assert updated.yes is True
    assert updated.ssh_port == 2222


def test_apply_deploy_workflow_answers_maps_confirmation_to_yes() -> None:
    request = DeployRequest()

    updated = apply_deploy_workflow_answers(request, DeployWorkflowAnswers(ip="198.51.100.10", confirm=True))
    unconfirmed = apply_deploy_workflow_answers(request, DeployWorkflowAnswers(ip="198.51.100.10", confirm=False))

    assert updated.yes is True
    assert unconfirmed.yes is False


def test_deploy_workflow_answers_validate_human_readable_input_errors() -> None:
    with pytest.raises(ValidationError) as exc_info:
        DeployWorkflowAnswers(ip="not-an-ip", user="bad user", client_name="bad name!")

    hint = validation_error_hint(exc_info.value)
    assert "ip: Enter a valid IP address, or use 'local' when running Meridian on the target server." in hint
    assert "user: Use letters, numbers, dots, hyphens, and underscores." in hint
    assert "client_name: Use letters, numbers, hyphens, and underscores." in hint


def test_apply_deploy_workflow_answers_revalidates_constructed_answers() -> None:
    request = DeployRequest()
    answers = DeployWorkflowAnswers.model_construct(
        ip="198.51.100.10",
        user="bad user",
        sni="www.microsoft.com",
        domain="",
        harden=True,
        client_name="default",
        server_name="Family VPN",
        icon="",
        color="ocean",
        pq=False,
        warp=False,
        geo_block=True,
        confirm=True,
    )

    with pytest.raises(ValidationError) as exc_info:
        apply_deploy_workflow_answers(request, answers)

    assert "user: Use letters, numbers, dots, hyphens, and underscores." in validation_error_hint(exc_info.value)
