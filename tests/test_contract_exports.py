"""Tests for generated meridian-core contract artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from meridian.core.events import EVENT_TYPES
from meridian.core.models import Event
from meridian.core.schema import DeployOutputEnvelope, command_catalog, schema_names
from meridian.core.services import collect_workflow, workflow_catalog
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


def test_checked_in_contracts_are_current() -> None:
    assert check_contracts(CONTRACTS) == []


def test_command_catalog_schema_references_are_exported() -> None:
    names = set(schema_names())

    for contract in command_catalog():
        assert contract["envelope_schema"] in names
        assert contract["data_schema"] in names
        assert contract["failure_data_schema"] in names
        assert contract["error_schema"] in names


def test_workflow_catalog_exports_deploy_workflow() -> None:
    catalog = workflow_catalog()
    deploy = collect_workflow("deploy")
    server = collect_workflow("server-onboarding")

    assert [item.id for item in catalog] == ["server-onboarding", "deploy"]
    assert catalog[0].ready_request_schema == "server-connection-draft"
    assert catalog[0].title == server.title
    assert catalog[1].ready_request_schema == "deploy-request"
    assert catalog[1].title == deploy.title
    assert catalog[1].summary == deploy.summary

    exported = json.loads((CONTRACTS / "workflows" / "index.json").read_text(encoding="utf-8"))
    assert exported["schema"] == "meridian.workflow-catalog/v1"
    assert [workflow["id"] for workflow in exported["workflows"]] == ["server-onboarding", "deploy"]


def test_event_schema_uses_public_literal_event_types() -> None:
    schema = json.loads((CONTRACTS / "schemas" / "event.schema.json").read_text(encoding="utf-8"))
    catalog = json.loads((CONTRACTS / "events.json").read_text(encoding="utf-8"))

    assert schema["properties"]["type"]["enum"] == list(EVENT_TYPES)
    assert catalog == {"schema": "meridian.event-types/v1", "event_schema": "event", "types": list(EVENT_TYPES)}


def test_deploy_fixtures_validate_and_jsonl_is_monotonic() -> None:
    for name in (
        "deploy-dry-run-envelope.json",
        "deploy-success-envelope.json",
        "deploy-user-error-envelope.json",
    ):
        payload = json.loads((CONTRACTS / "fixtures" / name).read_text(encoding="utf-8"))
        parsed = DeployOutputEnvelope.model_validate(payload)
        assert parsed.root.command == "deploy"

    dry_run = json.loads((CONTRACTS / "fixtures" / "deploy-dry-run-envelope.json").read_text(encoding="utf-8"))
    assert dry_run["status"] == "ok"
    assert dry_run["summary"]["changed"] is False
    assert dry_run["data"]["mode"] == "first_deploy"
    assert dry_run["data"]["server_ip"] == "198.51.100.10"
    assert dry_run["data"]["secret_path"] == "[redacted]"
    assert dry_run["data"]["xhttp_path"] == "0" * 16
    assert dry_run["data"]["ws_path"] == "0" * 16
    assert dry_run["data"]["info_page_path"] == "0" * 16
    assert "ssh_user" not in dry_run["data"]
    assert "panel_url" not in dry_run["data"]
    assert "client_name" not in dry_run["data"]

    events = _load_events("deploy-events.jsonl")
    assert [event.seq for event in events] == [1, 2, 3]
    assert events[-1].type == "command.completed"
    assert events[-1].data == {"command": "deploy", "mode": "first_deploy", "server_ip": "198.51.100.10"}

    dry_run_events = _load_events("deploy-dry-run-events.jsonl")
    assert [event.seq for event in dry_run_events] == [1, 2]
    assert [event.type for event in dry_run_events] == ["command.started", "command.completed"]
    assert [event.data["dry_run"] for event in dry_run_events] == [True, True]
    assert dry_run_events[-1].data == {
        "command": "deploy",
        "dry_run": True,
        "mode": "first_deploy",
        "server_ip": "198.51.100.10",
    }
    assert all("final_envelope" not in event.data for event in events + dry_run_events)


def test_contract_fixtures_are_redacted_passive_artifacts() -> None:
    fixtures = "\n".join(path.read_text(encoding="utf-8") for path in (CONTRACTS / "fixtures").glob("*"))

    assert "panel-secret" not in fixtures
    assert "api_token" not in fixtures
    assert "admin_pass" not in fixtures
    assert "private_key" not in fixtures
    assert "postgres://" not in fixtures


def _load_events(name: str) -> list[Event]:
    lines = [
        json.loads(line)
        for line in (CONTRACTS / "fixtures" / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [Event.model_validate(line) for line in lines]
