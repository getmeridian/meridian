"""Tests for meridian-core JSON Schema catalog."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from meridian.core.clients import build_client_list_result
from meridian.core.deploy import DeployResult
from meridian.core.deploy_planning import DeployClusterState, build_deploy_plan
from meridian.core.models import MeridianError
from meridian.core.output import OperationTimer, command_envelope, envelope
from meridian.core.schema import (
    ApiCommandsResult,
    ApiSchemasResult,
    DeployOutputEnvelope,
    PlanOutputEnvelope,
    command_catalog,
    schema_catalog,
    schema_for,
    schema_names,
)


def test_schema_catalog_lists_public_contracts() -> None:
    names = schema_names()

    assert "output-envelope" in names
    assert "apply" in names
    assert "apply-envelope" in names
    assert "apply-failure" in names
    assert "api-commands-envelope" in names
    assert "api-schema-envelope" in names
    assert "api-schemas-envelope" in names
    assert "api-workflow" in names
    assert "api-workflow-envelope" in names
    assert "client-list-envelope" in names
    assert "client-show-envelope" in names
    assert "client-name-request" in names
    assert "deploy-command-data" in names
    assert "deploy-envelope" in names
    assert "deploy-request" in names
    assert "deploy-result" in names
    assert "deploy-workflow-answers" in names
    assert "server-add-request" in names
    assert "node-add-request" in names
    assert "relay-deploy-request" in names
    assert "deploy-plan" in names
    assert "deploy-ports" in names
    assert "deploy-cluster-state" in names
    assert "deploy-node-state" in names
    assert "workflow-plan" in names
    assert "input-field" in names
    assert "input-option" in names
    assert "input-section" in names
    assert "plan-envelope" in names
    assert "fleet-status-envelope" in names
    assert "fleet-inventory-envelope" in names
    assert "event" in names
    assert "schema-catalog-entry" in names
    assert "command-contract" in names
    assert "command-catalog-entry" in names
    assert "empty-data" in names
    assert "plan-result" in names
    assert "fleet-status" in names
    assert "fleet-inventory" in names


def test_schema_for_output_envelope_uses_wire_aliases() -> None:
    schema = schema_for("output-envelope")

    assert "schema" in schema["properties"]
    assert "schema_version" not in schema["properties"]
    assert schema["properties"]["status"]["enum"] == ["ok", "changed", "no_changes", "failed", "cancelled"]


def test_request_schemas_expose_model_validation_constraints() -> None:
    server_add = schema_for("server-add-request")["properties"]
    deploy = schema_for("deploy-request")["properties"]
    workflow_answers = schema_for("deploy-workflow-answers")["properties"]

    assert server_add["ip"]["anyOf"] == [
        {"format": "ipv4", "type": "string"},
        {"format": "ipv6", "type": "string"},
    ]
    assert server_add["user"]["pattern"] == r"^[a-zA-Z0-9._-]+$"
    assert server_add["user"]["minLength"] == 1
    assert server_add["name"]["pattern"] == r"^$|^[a-zA-Z0-9][a-zA-Z0-9_-]*$"
    assert deploy["ip"]["anyOf"] == [
        {"format": "ipv4", "type": "string"},
        {"format": "ipv6", "type": "string"},
        {"pattern": r"^$|^[Ll][Oo][Cc][Aa][Ll](?:[Ll][Yy])?$", "type": "string"},
    ]
    assert deploy["client_name"]["pattern"] == r"^$|^[a-zA-Z0-9][a-zA-Z0-9_-]*$"
    assert workflow_answers["ip"]["anyOf"] == deploy["ip"]["anyOf"]
    assert workflow_answers["user"]["pattern"] == deploy["user"]["pattern"]


def test_command_envelope_schema_binds_command_to_typed_data() -> None:
    schema = schema_for("fleet-status-envelope")

    assert schema["discriminator"]["propertyName"] == "status"
    assert len(schema["oneOf"]) == 2
    refs = [option["$ref"] for option in schema["oneOf"]]
    success_ref = next(ref for ref in refs if ref.endswith("/_FleetStatusSuccessEnvelope"))
    terminal_ref = next(ref for ref in refs if ref.endswith("/_FleetStatusTerminalEnvelope"))
    success = schema["$defs"][success_ref.rsplit("/", 1)[-1]]
    terminal = schema["$defs"][terminal_ref.rsplit("/", 1)[-1]]
    assert success["properties"]["command"]["const"] == "fleet.status"
    assert success["properties"]["data"]["$ref"].endswith("/FleetStatus")
    assert terminal["properties"]["data"]["$ref"].endswith("/EmptyData")
    assert set(terminal["required"]) >= {"schema", "command", "data", "errors", "warnings"}


def test_schema_catalog_can_include_full_schemas() -> None:
    catalog = schema_catalog(include_schemas=True)
    output = next(item for item in catalog if item["name"] == "output-envelope")
    plan = next(item for item in catalog if item["name"] == "plan-envelope")

    assert output["title"] == "OutputEnvelope"
    assert output["schema"]["properties"]["command"]["type"] == "string"
    assert plan["commands"] == ["plan"]
    assert "oneOf" in plan["schema"]


def test_schema_catalog_entries_are_typed() -> None:
    catalog = schema_catalog(include_schemas=True)

    parsed = ApiSchemasResult.model_validate({"schemas": catalog})
    entry = next(item for item in parsed.schemas if item.name == "plan-envelope")

    assert entry.commands == ["plan"]
    assert entry.json_schema is not None
    assert next(item for item in catalog if item["name"] == "plan-envelope")["schema"] == entry.json_schema


def test_command_catalog_maps_commands_to_envelope_and_data_schemas() -> None:
    catalog = command_catalog()
    by_command = {item["command"]: item for item in catalog}

    assert by_command["plan"]["envelope_schema"] == "plan-envelope"
    assert by_command["plan"]["data_schema"] == "plan-result"
    assert by_command["plan"]["failure_data_schema"] == "empty-data"
    assert by_command["plan"]["error_schema"] == "error"
    assert by_command["plan"]["machine_flags"] == ["--json"]
    assert by_command["plan"]["argv"] == ["plan"]
    assert "changed" in by_command["plan"]["statuses"]
    assert {"status": "changed", "exit_code": 2, "category": "none", "meaning": "changes pending"} in by_command[
        "plan"
    ]["outcomes"]
    assert {
        "status": "failed",
        "exit_code": 2,
        "category": "user",
        "meaning": "user or configuration error",
    } in by_command["plan"]["outcomes"]
    assert by_command["client.list"]["data_schema"] == "client-list"
    assert by_command["apply"]["data_schema"] == "apply"
    assert by_command["apply"]["failure_data_schema"] == "apply-failure"
    assert by_command["apply"]["machine_flags"] == ["--json"]
    assert by_command["apply"]["stability"] == "preview"
    assert by_command["deploy"]["envelope_schema"] == "deploy-envelope"
    assert by_command["deploy"]["data_schema"] == "deploy-command-data"
    assert by_command["deploy"]["machine_flags"] == ["--json", "--events=jsonl", "--request", "--dry-run"]
    assert by_command["api.commands"]["data_schema"] == "api-commands"
    assert by_command["api.workflow"]["data_schema"] == "api-workflow"
    assert by_command["api.workflow"]["machine_flags"] == ["--json"]
    assert by_command["fleet.status"]["data_schema"] == "fleet-status"
    assert by_command["fleet.inventory"]["statuses"] == ["ok", "failed", "cancelled"]
    assert by_command["fleet.inventory"]["exit_codes"]["130"] == "cancelled by the user"
    for contract in by_command.values():
        assert contract["exit_codes"]["1"] == "unexpected Meridian bug"


def test_command_catalog_can_embed_command_schemas() -> None:
    catalog = command_catalog(include_schemas=True)
    plan = next(item for item in catalog if item["command"] == "plan")
    parsed = ApiCommandsResult.model_validate({"commands": catalog})

    success_ref = next(
        option["$ref"] for option in plan["envelope"]["oneOf"] if option["$ref"].endswith("/_PlanSuccessEnvelope")
    )
    success = plan["envelope"]["$defs"][success_ref.rsplit("/", 1)[-1]]
    assert success["properties"]["command"]["const"] == "plan"
    assert plan["data"]["title"] == "PlanResult"
    assert plan["failure_data"]["title"] == "EmptyData"
    assert plan["error"]["title"] == "MeridianError"
    assert next(item for item in parsed.commands if item.command == "plan").data is not None


def test_command_envelope_schema_validates_failed_output_shape() -> None:
    error = MeridianError(
        code="MERIDIAN_USER_ERROR",
        category="user",
        message="No desired state defined in cluster.yml",
        exit_code=2,
    )
    payload = envelope(
        command="plan",
        summary="No desired state defined in cluster.yml",
        status="failed",
        exit_code=2,
        errors=[error],
        timer=OperationTimer(started_at="2026-05-04T21:00:00Z", operation_id="op-test"),
    )

    parsed = PlanOutputEnvelope.model_validate(payload.model_dump(mode="json", by_alias=True))

    assert parsed.root.command == "plan"
    assert parsed.root.status == "failed"
    assert parsed.root.data.model_dump() == {}


def test_command_envelope_producer_validates_migrated_commands() -> None:
    with pytest.raises(ValidationError):
        command_envelope(
            command="plan",
            summary="Plan has changes",
            status="changed",
            exit_code=2,
            timer=OperationTimer(started_at="2026-05-04T21:00:00Z", operation_id="op-test"),
        )


def test_command_envelope_producer_rejects_unsupported_success_exit_code() -> None:
    with pytest.raises(ValueError, match="unsupported outcome"):
        command_envelope(
            command="client.list",
            data=build_client_list_result([]).to_data(),
            summary="0 clients",
            status="ok",
            exit_code=2,
            timer=OperationTimer(started_at="2026-05-04T21:00:00Z", operation_id="op-test"),
        )


def test_command_envelope_producer_rejects_unsupported_failure_exit_code() -> None:
    error = MeridianError(
        code="MERIDIAN_USER_ERROR",
        category="user",
        message="User error",
        exit_code=2,
    )

    with pytest.raises(ValueError, match="unsupported outcome"):
        command_envelope(
            command="client.list",
            summary="User error",
            status="failed",
            exit_code=0,
            errors=[error],
            timer=OperationTimer(started_at="2026-05-04T21:00:00Z", operation_id="op-test"),
        )


def test_command_envelope_schema_rejects_failed_output_without_error() -> None:
    payload = envelope(
        command="plan",
        summary="No desired state defined in cluster.yml",
        status="failed",
        exit_code=2,
        timer=OperationTimer(started_at="2026-05-04T21:00:00Z", operation_id="op-test"),
    )

    with pytest.raises(ValidationError):
        PlanOutputEnvelope.model_validate(payload.model_dump(mode="json", by_alias=True))


def test_command_envelope_schema_rejects_success_without_typed_data() -> None:
    payload = envelope(
        command="plan",
        summary="Plan has changes",
        status="changed",
        exit_code=2,
        timer=OperationTimer(started_at="2026-05-04T21:00:00Z", operation_id="op-test"),
    )

    with pytest.raises(ValidationError):
        PlanOutputEnvelope.model_validate(payload.model_dump(mode="json", by_alias=True))


def test_deploy_command_envelope_accepts_result_and_dry_run_plan() -> None:
    result = DeployResult(
        mode="first_deploy",
        server_ip="198.51.100.10",
        ssh_user="root",
        ssh_port=22,
        domain="vpn.example",
        sni="www.microsoft.com",
        client_name="default",
        harden=True,
        pq=False,
        warp=False,
        geo_block=True,
        panel_url="https://198.51.100.10/panel",
        panel_secret_path="secret_path",
        connection_page_path="connection_path",
        node_count=1,
        relay_count=0,
        summary="Deploy completed for 198.51.100.10",
    )
    deployed = command_envelope(
        command="deploy",
        data=result.model_dump(mode="json"),
        summary=result.summary,
        status="changed",
        exit_code=0,
        timer=OperationTimer(started_at="2026-05-04T21:00:00Z", operation_id="op-deploy-result"),
    )

    parsed_result = DeployOutputEnvelope.model_validate(deployed.model_dump(mode="json", by_alias=True))

    assert parsed_result.root.status == "changed"
    assert parsed_result.root.data.server_ip == "198.51.100.10"

    plan = build_deploy_plan(
        "198.51.100.10",
        DeployClusterState(
            is_configured=False,
            panel_secret_path="",
            panel_sub_path="",
            node_count=0,
            relay_count=0,
        ),
        token_hex=lambda n: "0" * (n * 2),
    )
    planned = command_envelope(
        command="deploy",
        data=plan.model_dump(mode="json"),
        summary="Deploy plan",
        status="ok",
        exit_code=0,
        timer=OperationTimer(started_at="2026-05-04T21:00:00Z", operation_id="op-deploy-plan"),
    )

    parsed_plan = DeployOutputEnvelope.model_validate(planned.model_dump(mode="json", by_alias=True))

    assert parsed_plan.root.status == "ok"
    assert parsed_plan.root.data.mode == "first_deploy"

    with pytest.raises(ValidationError):
        DeployOutputEnvelope.model_validate(
            command_envelope(
                command="deploy",
                data=result.model_dump(mode="json"),
                summary=result.summary,
                status="ok",
                exit_code=0,
                timer=OperationTimer(started_at="2026-05-04T21:00:00Z", operation_id="op-deploy-bad-result"),
            ).model_dump(mode="json", by_alias=True)
        )

    with pytest.raises(ValidationError):
        DeployOutputEnvelope.model_validate(
            command_envelope(
                command="deploy",
                data=plan.model_dump(mode="json"),
                summary="Deploy plan",
                status="changed",
                exit_code=0,
                timer=OperationTimer(started_at="2026-05-04T21:00:00Z", operation_id="op-deploy-bad-plan"),
            ).model_dump(mode="json", by_alias=True)
        )


def test_unknown_schema_name_is_actionable() -> None:
    with pytest.raises(ValueError, match="Available:"):
        schema_for("missing")
