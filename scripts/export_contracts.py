"""Export deterministic meridian-core contracts for API and Studio clients."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

from meridian.cli_catalog import command_catalog
from meridian.control.openapi import openapi_document
from meridian.core.agent_http import AGENT_HTTP_SURFACE
from meridian.core.deploy import DeployRequest, DeployResult
from meridian.core.models import ErrorModel, Event, OutputEnvelope, OutputStatus, Summary
from meridian.core.output import EVENT_SCHEMA, OUTPUT_SCHEMA, json_dumps, jsonl_dumps
from meridian.core.schema import (
    event_type_catalog,
    schema_for,
    schema_names,
    validate_command_envelope,
)
from meridian.core.schema_agent import AGENT_SCHEMAS, agent_schema
from meridian.core.services.workflows import collect_workflow, workflow_catalog
from meridian.setup.single_server import PENDING_SERVER_REF, preview_single_server_deploy

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "contracts" / "meridian" / "v1"
FIXED_TIME = "2026-05-04T21:00:00Z"
# Fixtures are examples: a fixed version keeps release bumps from rewriting them.
FIXTURE_VERSION = "4.0.0"


def _plain_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _redacted_json(value: Any) -> str:
    return json_dumps(value) + "\n"


def _envelope(
    *,
    command: str,
    operation_id: str,
    status: OutputStatus,
    exit_code: int,
    summary: Summary,
    data: dict[str, Any] | None = None,
    errors: list[ErrorModel] | None = None,
) -> OutputEnvelope:
    payload = OutputEnvelope(
        schema=OUTPUT_SCHEMA,
        meridian_version=FIXTURE_VERSION,
        command=command,
        operation_id=operation_id,
        started_at=FIXED_TIME,
        duration_ms=0,
        status=status,
        exit_code=exit_code,
        summary=summary,
        data=data or {},
        warnings=[],
        errors=errors or [],
    )
    return validate_command_envelope(payload)


def _deploy_dry_run_fixture() -> OutputEnvelope:
    # A fresh deploy draws random transport paths; fix them so the fixture stays byte-stable.
    with patch("meridian.setup.default_paths.secrets.token_hex", return_value="0123456789abcdef"):
        plan = preview_single_server_deploy(
            None,
            DeployRequest(ip="198.51.100.10"),
            server_ref=PENDING_SERVER_REF,
            server_ip="198.51.100.10",
        )
    return _envelope(
        command="deploy",
        operation_id="op-fixture-deploy-dry-run",
        status="ok",
        exit_code=0,
        summary=Summary(
            text=f"Deploy plan: {plan.mode} for {plan.server_ip}",
            changed=False,
            counts={"nodes": plan.node_count, "relays": plan.relay_count},
        ),
        data=plan.model_dump(mode="json"),
    )


def _deploy_success_fixture(operation_id: str = "op-fixture-deploy-success") -> OutputEnvelope:
    result = DeployResult(
        mode="first_deploy",
        server_ip="198.51.100.10",
        ssh_user="root",
        ssh_port=22,
        domain="vpn.example",
        sni="www.google.com",
        client_name="default",
        harden=True,
        warp=False,
        geo_block=True,
        panel_url="https://vpn.example/panel-secret/",
        panel_secret_path="panel-secret",
        connection_page_path="connect",
        node_count=1,
        relay_count=0,
        summary="Deploy completed for 198.51.100.10",
    )
    return _envelope(
        command="deploy",
        operation_id=operation_id,
        status="changed",
        exit_code=0,
        summary=Summary(text=result.summary, changed=True, counts={"nodes": 1, "relays": 0}),
        data=result.model_dump(mode="json"),
    )


def _deploy_user_error_fixture() -> OutputEnvelope:
    error = ErrorModel(
        code="MERIDIAN_DEPLOY_INPUT_REQUIRED",
        category="user",
        message="Deploy request is incomplete",
        hint="Collect the deploy workflow fields, then pass a confirmed deploy request.",
        retryable=True,
        exit_code=2,
    )
    return _envelope(
        command="deploy",
        operation_id="op-fixture-deploy-user-error",
        status="failed",
        exit_code=2,
        summary=Summary(text="Deploy request is incomplete", changed=False),
        errors=[error],
    )


def _deploy_event_fixture() -> str:
    operation_id = "op-fixture-deploy-events"
    events = [
        Event(
            schema=EVENT_SCHEMA,
            operation_id=operation_id,
            seq=1,
            time=FIXED_TIME,
            level="info",
            type="command.started",
            phase="deploy",
            message="Deploy started",
            data={"command": "deploy"},
        ),
        Event(
            schema=EVENT_SCHEMA,
            operation_id=operation_id,
            seq=2,
            time=FIXED_TIME,
            level="info",
            type="provision.step.started",
            phase="provision",
            message="Install Docker",
            data={"step": "Install Docker", "index": 1, "total": 1},
        ),
        Event(
            schema=EVENT_SCHEMA,
            operation_id=operation_id,
            seq=3,
            time=FIXED_TIME,
            level="info",
            type="command.completed",
            phase="deploy",
            message="Deploy completed for 198.51.100.10",
            data={"command": "deploy", "mode": "first_deploy", "server_ip": "198.51.100.10"},
        ),
    ]
    return "".join(jsonl_dumps(event) + "\n" for event in events)


def _deploy_dry_run_event_fixture() -> str:
    operation_id = "op-fixture-deploy-dry-run-events"
    events = [
        Event(
            schema=EVENT_SCHEMA,
            operation_id=operation_id,
            seq=1,
            time=FIXED_TIME,
            level="info",
            type="command.started",
            phase="deploy",
            message="Deploy dry-run started",
            data={"command": "deploy", "dry_run": True},
        ),
        Event(
            schema=EVENT_SCHEMA,
            operation_id=operation_id,
            seq=2,
            time=FIXED_TIME,
            level="info",
            type="command.completed",
            phase="deploy",
            message="Deploy dry-run completed",
            data={
                "command": "deploy",
                "dry_run": True,
                "mode": "first_deploy",
                "server_ip": "198.51.100.10",
            },
        ),
    ]
    return "".join(jsonl_dumps(event) + "\n" for event in events)


def build_contract_files() -> dict[Path, str]:
    """Return contract file paths relative to the output directory."""
    files: dict[Path, str] = {}

    for name in schema_names():
        files[Path("schemas") / f"{name}.schema.json"] = _plain_json(schema_for(name))

    files[Path("commands.json")] = _plain_json({"schema": "meridian.command-catalog/v1", "commands": command_catalog()})
    files[Path("events.json")] = _plain_json(event_type_catalog())
    files[Path("admin") / "openapi.json"] = _plain_json(openapi_document().to_data())
    # The service-to-agent protocol is internal (consumed by the Rust agent), so it stays out of `schemas/`.
    for name in sorted(AGENT_SCHEMAS):
        files[Path("agent") / "schemas" / f"{name}.schema.json"] = _plain_json(agent_schema(name))
    files[Path("agent") / "http.json"] = _plain_json(AGENT_HTTP_SURFACE.to_data())

    workflows = workflow_catalog()
    files[Path("workflows") / "index.json"] = _plain_json(
        {"schema": "meridian.workflow-catalog/v1", "workflows": [item.model_dump(mode="json") for item in workflows]}
    )
    files[Path("workflows") / "server-onboarding.json"] = _plain_json(
        collect_workflow("server-onboarding").model_dump(mode="json")
    )
    files[Path("workflows") / "deploy.json"] = _plain_json(collect_workflow("deploy").model_dump(mode="json"))

    files[Path("fixtures") / "deploy-dry-run-envelope.json"] = _redacted_json(_deploy_dry_run_fixture())
    files[Path("fixtures") / "deploy-success-envelope.json"] = _redacted_json(_deploy_success_fixture())
    files[Path("fixtures") / "deploy-user-error-envelope.json"] = _redacted_json(_deploy_user_error_fixture())
    files[Path("fixtures") / "deploy-events.jsonl"] = _deploy_event_fixture()
    files[Path("fixtures") / "deploy-dry-run-events.jsonl"] = _deploy_dry_run_event_fixture()

    return dict(sorted(files.items(), key=lambda item: item[0].as_posix()))


def write_contracts(output_dir: Path) -> None:
    files = build_contract_files()
    if output_dir.exists():
        shutil.rmtree(output_dir)
    for relative, content in files.items():
        path = output_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def check_contracts(output_dir: Path) -> list[str]:
    expected = build_contract_files()
    errors: list[str] = []

    actual_paths = (
        {path.relative_to(output_dir) for path in output_dir.rglob("*") if path.is_file()}
        if output_dir.exists()
        else set()
    )
    expected_paths = set(expected)

    for missing in sorted(expected_paths - actual_paths):
        errors.append(f"missing: {missing.as_posix()}")
    for extra in sorted(actual_paths - expected_paths):
        errors.append(f"extra: {extra.as_posix()}")
    for relative in sorted(expected_paths & actual_paths):
        actual = (output_dir / relative).read_text(encoding="utf-8")
        if actual != expected[relative]:
            errors.append(f"changed: {relative.as_posix()}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if checked-in contracts are out of date")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output directory")
    args = parser.parse_args(argv)

    output_dir = args.output.resolve()
    if args.check:
        errors = check_contracts(output_dir)
        if errors:
            print("Contract artifacts are out of date:", file=sys.stderr)
            for error in errors:
                print(f"  {error}", file=sys.stderr)
            print("Run: uv run python scripts/export_contracts.py", file=sys.stderr)
            return 1
        print(f"OK: contracts current in {output_dir}")
        return 0

    write_contracts(output_dir)
    print(f"Wrote contracts to {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
