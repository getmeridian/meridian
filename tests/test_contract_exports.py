"""Tests for generated meridian-core contract artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meridian.cli_catalog import command_catalog
from meridian.core.events import EVENT_TYPES
from meridian.core.schema import schema_names
from meridian.core.services.workflows import WorkflowNotFoundError, collect_workflow
from scripts.export_contracts import check_contracts, write_contracts

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts" / "meridian" / "v1"


def test_contract_export_is_deterministic_and_checkable(tmp_path: Path) -> None:
    output = tmp_path / "contracts"

    write_contracts(output)
    first = {
        path.relative_to(output).as_posix(): path.read_text(encoding="utf-8")
        for path in output.rglob("*")
        if path.is_file()
    }
    write_contracts(output)
    second = {
        path.relative_to(output).as_posix(): path.read_text(encoding="utf-8")
        for path in output.rglob("*")
        if path.is_file()
    }

    assert first == second
    assert check_contracts(output) == []

    commands = output / "commands.json"
    commands.write_text(commands.read_text(encoding="utf-8").replace("deploy", "deploy-drift", 1), encoding="utf-8")
    assert "changed: commands.json" in check_contracts(output)


def test_verification_contracts_and_commands_are_exported(tmp_path: Path) -> None:
    output = tmp_path / "contracts"

    write_contracts(output)

    for name in (
        "probe-envelope",
        "probe-result",
        "test-envelope",
        "test-result",
        "verification-check",
        "verification-finding",
    ):
        assert (output / "schemas" / f"{name}.schema.json").is_file()

    commands = json.loads((output / "commands.json").read_text(encoding="utf-8"))
    by_command = {item["command"]: item for item in commands["commands"]}

    assert by_command["probe"]["failure_data_schema"] == "probe-failure"
    assert by_command["probe"]["exit_codes"]["4"] == "probe completed with negative findings"
    assert by_command["test"]["failure_data_schema"] == "test-failure"
    assert by_command["test"]["statuses"] == ["ok", "failed"]
    assert by_command["test"]["interrupt_behavior"] == "exit_130_without_envelope"


def test_checked_in_contracts_are_current() -> None:
    assert check_contracts(CONTRACTS) == []


def test_command_catalog_schema_references_are_exported() -> None:
    names = set(schema_names())

    for contract in command_catalog():
        assert contract["envelope_schema"] in names
        assert contract["data_schema"] in names
        assert contract["failure_data_schema"] in names
        assert contract["error_schema"] in names


def test_deploy_workflow_names_the_request_schema_it_collects() -> None:
    deploy = collect_workflow("deploy")

    assert deploy.ready_request_schema == "deploy-request"
    assert "deploy-request" in schema_names()
    with pytest.raises(WorkflowNotFoundError):
        collect_workflow("server-onboarding")


def test_event_schema_uses_public_literal_event_types() -> None:
    schema = json.loads((CONTRACTS / "schemas" / "event.schema.json").read_text(encoding="utf-8"))
    catalog = json.loads((CONTRACTS / "events.json").read_text(encoding="utf-8"))

    assert schema["properties"]["type"]["enum"] == list(EVENT_TYPES)
    assert catalog == {"schema": "meridian.event-types/v1", "event_schema": "event", "types": list(EVENT_TYPES)}
