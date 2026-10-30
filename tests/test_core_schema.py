"""Tests for meridian-core JSON Schema catalog."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from meridian.cli_catalog import command_catalog, command_contracts
from meridian.core.clients import build_client_list_result
from meridian.core.command_catalog import CommandContract, command_output, command_schema_bindings
from meridian.core.deploy import DeployPlan, DeployPorts, DeployResult
from meridian.core.models import ErrorModel
from meridian.core.output import OperationTimer, command_envelope, envelope
from meridian.core.schema import (
    ApiCommandsResult,
    ApiSchemasResult,
    DeployOutputEnvelope,
    PlanOutputEnvelope,
    ProbeOutputEnvelope,
    schema_catalog,
    schema_for,
)
from meridian.core.schema import (
    TestOutputEnvelope as VerificationTestOutputEnvelope,
)
from meridian.core.verification import (
    ProbeResult,
    VerificationCheck,
    VerificationFinding,
    VerificationTarget,
)
from meridian.core.verification import (
    TestResult as VerificationTestResult,
)


def test_schema_for_output_envelope_uses_wire_aliases() -> None:
    schema = schema_for("output-envelope")

    assert "schema" in schema["properties"]
    assert "schema_version" not in schema["properties"]
    assert schema["properties"]["status"]["enum"] == ["ok", "changed", "no_changes", "failed", "cancelled"]


def test_request_schemas_expose_model_validation_constraints() -> None:
    server_add = schema_for("server-add-request")["properties"]
    server_draft = schema_for("server-connection-draft")["properties"]
    server_profile = schema_for("server-profile")["properties"]
    deploy = schema_for("deploy-request")["properties"]

    assert server_add["ip"]["anyOf"] == [
        {"format": "ipv4", "type": "string"},
        {"format": "ipv6", "type": "string"},
    ]
    assert server_add["user"]["pattern"] == r"^[a-zA-Z0-9._-]+$"
    assert server_add["user"]["minLength"] == 1
    assert server_add["name"]["pattern"] == r"^$|^[a-zA-Z0-9][a-zA-Z0-9_-]*$"
    assert server_add["ssh_port"]["minimum"] == 1
    assert server_add["ssh_port"]["maximum"] == 65535
    assert server_draft["host"]["anyOf"] == [
        {"format": "ipv4", "type": "string"},
        {"format": "ipv6", "type": "string"},
    ]
    assert server_draft["title"]["maxLength"] == 80
    assert server_profile["id"]["pattern"] == r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$"
    assert deploy["ip"]["anyOf"] == [
        {"format": "ipv4", "type": "string"},
        {"format": "ipv6", "type": "string"},
        {"pattern": r"^$|^[Ll][Oo][Cc][Aa][Ll](?:[Ll][Yy])?$", "type": "string"},
    ]
    assert deploy["client_name"]["pattern"] == r"^$|^[a-zA-Z0-9][a-zA-Z0-9_-]*$"
    assert deploy["requested_server"]["pattern"] == r"^$|^[^\r\n\t]+$"
    # Deploy requests leave the user empty to mean "saved, else root".
    assert deploy["user"]["pattern"] == "^$|" + server_add["user"]["pattern"]


def test_topology_schemas_model_server_capabilities() -> None:
    shelf_item = schema_for("topology-server-shelf-item")["properties"]

    assert shelf_item["capabilities"]["items"]["enum"] == [
        "panel",
        "exit",
        "relay",
        "routing_gateway",
    ]
    assert shelf_item["region"]["pattern"] == r"^$|^[A-Za-z]{2}$"


def test_connection_test_schema_exposes_scope_and_client() -> None:
    properties = schema_for("test-result")["properties"]

    assert properties["scope"]["enum"] == ["basic", "full"]
    assert properties["scope"]["default"] == "full"
    assert properties["client"] == {"default": "", "title": "Client", "type": "string"}


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


def test_command_catalog_distinguishes_process_interrupts_from_json_outcomes() -> None:
    for contract in command_catalog(include_schemas=True):
        assert "unexpected Meridian bug" in contract["exit_codes"]["1"], contract["command"]
        assert contract["exit_codes"]["130"] == "process interrupted; no JSON envelope is emitted"
        assert contract["interrupt_behavior"] == "exit_130_without_envelope"
        assert all(outcome["exit_code"] != 130 for outcome in contract["outcomes"])
        # A cooperative cancel is a completed JSON outcome with its own exit code.
        cancelled = [outcome for outcome in contract["outcomes"] if outcome["status"] == "cancelled"]
        assert ("cancelled" in contract["statuses"]) == bool(cancelled), contract["command"]
        assert all(outcome["exit_code"] == 5 and outcome["category"] == "cancelled" for outcome in cancelled)
        envelope = contract["envelope"]
        terminal_ref = next(option["$ref"] for option in envelope["oneOf"] if "TerminalEnvelope" in option["$ref"])
        terminal = envelope["$defs"][terminal_ref.rsplit("/", 1)[-1]]
        assert terminal["properties"]["status"]["const"] == "failed"


@pytest.mark.parametrize(
    ("command", "envelope_name", "data_model"),
    [
        ("client.add", "client-add-envelope", "ClientAddResult"),
        ("client.remove", "client-remove-envelope", "ClientRemoveResult"),
        ("client.enable", "client-enable-envelope", "ClientStatusResult"),
        ("client.disable", "client-disable-envelope", "ClientStatusResult"),
        ("node.list", "node-list-envelope", "NodeListResult"),
        ("relay.list", "relay-list-envelope", "RelayListResult"),
    ],
)
def test_secondary_command_envelopes_bind_typed_success_and_empty_failure_data(
    command: str,
    envelope_name: str,
    data_model: str,
) -> None:
    schema = schema_for(envelope_name)
    success_ref = next(option["$ref"] for option in schema["oneOf"] if "SuccessEnvelope" in option["$ref"])
    terminal_ref = next(option["$ref"] for option in schema["oneOf"] if "TerminalEnvelope" in option["$ref"])
    success = schema["$defs"][success_ref.rsplit("/", 1)[-1]]
    terminal = schema["$defs"][terminal_ref.rsplit("/", 1)[-1]]

    assert success["properties"]["command"]["const"] == command
    assert success["properties"]["data"]["$ref"].endswith(f"/{data_model}")
    assert terminal["properties"]["data"]["$ref"].endswith("/EmptyData")


def test_command_contract_registry_is_public_and_schema_neutral() -> None:
    contracts = command_contracts()

    assert all(isinstance(contract, CommandContract) for contract in contracts)
    assert [contract.command for contract in contracts] == sorted(contract.command for contract in contracts)
    assert command_output("probe") is not None
    assert command_output("missing") is None
    assert command_schema_bindings()["test"] == "test-envelope"


def test_command_catalog_can_embed_command_schemas() -> None:
    catalog = command_catalog(include_schemas=True)
    plan = next(item for item in catalog if item["command"] == "plan")
    parsed = ApiCommandsResult.model_validate({"commands": catalog})

    success_ref = next(
        option["$ref"] for option in plan["envelope"]["oneOf"] if option["$ref"].endswith("/_PlanSuccessEnvelope")
    )
    success = plan["envelope"]["$defs"][success_ref.rsplit("/", 1)[-1]]
    assert success["properties"]["command"]["const"] == "plan"
    assert plan["data"]["title"] == "PlanCommandData"
    resource_ref = plan["data"]["properties"]["resources"]["items"]["$ref"]
    resource = plan["data"]["$defs"][resource_ref.rsplit("/", 1)[-1]]
    assert set(resource["properties"]["kind"]["enum"]) >= {"node_runtime", "probe"}
    assert plan["failure_data"]["title"] == "PlanFailureData"
    assert plan["error"]["title"] == "MeridianError"
    assert next(item for item in parsed.commands if item.command == "plan").data is not None


def test_command_envelope_schema_validates_failed_output_shape() -> None:
    error = ErrorModel(
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
    error = ErrorModel(
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


def test_probe_envelope_accepts_completed_negative_findings() -> None:
    result = ProbeResult.from_checks(
        target=VerificationTarget(requested="198.51.100.20", resolved_ip="198.51.100.20"),
        checks=[
            VerificationCheck(
                id="tls.certificate",
                name="TLS certificate",
                status="failed",
                findings=[
                    VerificationFinding(
                        code="TLS_CERTIFICATE_MISMATCH",
                        status="failed",
                        message="The certificate does not match the expected deployment.",
                        remediation="Verify the configured domain and certificate.",
                    )
                ],
            )
        ],
    )
    payload = command_envelope(
        command="probe",
        data=result.to_data(),
        summary="Probe completed with findings",
        status="ok",
        exit_code=4,
        timer=OperationTimer(started_at="2026-05-04T21:00:00Z", operation_id="op-probe"),
    )

    parsed = ProbeOutputEnvelope.model_validate(payload.model_dump(mode="json", by_alias=True))

    assert parsed.root.status == "ok"
    assert parsed.root.exit_code == 4
    assert parsed.root.data.verdict == "findings"


def test_test_failure_envelope_accepts_partial_typed_result_or_empty_data() -> None:
    result = VerificationTestResult.from_checks(
        target=VerificationTarget(requested="edge.example"),
        checks=[
            VerificationCheck(
                id="proxy.connect",
                name="Proxy connection",
                status="skipped",
            )
        ],
    )
    error = ErrorModel(
        code="MERIDIAN_TEST_NETWORK_ERROR",
        category="system",
        message="The proxy endpoint could not be reached.",
        exit_code=3,
    )
    partial_payload = command_envelope(
        command="test",
        data=result.to_data(),
        summary="Connection test was inconclusive",
        status="failed",
        exit_code=3,
        errors=[error],
        timer=OperationTimer(started_at="2026-05-04T21:00:00Z", operation_id="op-test-partial"),
    )

    parsed_partial = VerificationTestOutputEnvelope.model_validate(
        partial_payload.model_dump(mode="json", by_alias=True)
    )

    assert parsed_partial.root.data.verdict == "inconclusive"
    assert parsed_partial.root.data.counts.skipped == 1

    empty_payload = command_envelope(
        command="test",
        summary="Invalid test target",
        status="failed",
        exit_code=2,
        errors=[
            ErrorModel(
                code="MERIDIAN_TEST_TARGET_INVALID",
                category="user",
                message="The test target is invalid.",
                exit_code=2,
            )
        ],
        timer=OperationTimer(started_at="2026-05-04T21:00:00Z", operation_id="op-test-empty"),
    )

    parsed_empty = VerificationTestOutputEnvelope.model_validate(empty_payload.model_dump(mode="json", by_alias=True))

    assert parsed_empty.root.data.model_dump() == {}


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

    plan = DeployPlan(
        mode="first_deploy",
        server_ip="198.51.100.10",
        ports=DeployPorts(xhttp_port=0, reality_port=10443, wss_port=0),
        secret_path="",
        xhttp_path="",
        ws_path="",
        info_page_path="",
        node_count=1,
        relay_count=0,
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
