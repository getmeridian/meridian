"""API contract inspection commands."""

from __future__ import annotations

import typer

from meridian.cli_catalog import command_catalog
from meridian.console import err_console, error_context, fail, is_json_mode
from meridian.core.models import ErrorModel, Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.schema import schema_catalog, schema_for
from meridian.core.services.workflows import WorkflowNotFoundError, collect_workflow
from meridian.renderers import emit_json


def run_schemas(*, include_schemas: bool = False) -> None:
    """List meridian-core schemas."""
    operation = OperationContext()
    with error_context("api.schemas", timer=operation.timer):
        catalog = schema_catalog(include_schemas=include_schemas and is_json_mode())
        if is_json_mode():
            emit_json(
                command_envelope(
                    command="api.schemas",
                    data={"schemas": catalog},
                    summary=Summary(text=f"{len(catalog)} schema(s)", changed=False, counts={"schemas": len(catalog)}),
                    timer=operation.timer,
                )
            )
            return

        err_console.print()
        err_console.print("  [bold]Meridian API schemas[/bold]")
        for item in catalog:
            err_console.print(f"    {item['name']}  [dim]{item['title']}[/dim]")
        err_console.print()


def run_commands(*, include_schemas: bool = False) -> None:
    """List machine-readable command contracts."""
    operation = OperationContext()
    with error_context("api.commands", timer=operation.timer):
        catalog = command_catalog(include_schemas=include_schemas and is_json_mode())
        if is_json_mode():
            emit_json(
                command_envelope(
                    command="api.commands",
                    data={"commands": catalog},
                    summary=Summary(
                        text=f"{len(catalog)} command contract(s)",
                        changed=False,
                        counts={"commands": len(catalog)},
                    ),
                    timer=operation.timer,
                )
            )
            return

        err_console.print()
        err_console.print("  [bold]Meridian API commands[/bold]")
        for item in catalog:
            err_console.print(f"    {item['command']}  [dim]{item['envelope_schema']}[/dim]")
        err_console.print()


def run_schema(name: str) -> None:
    """Print one meridian-core JSON Schema."""
    operation = OperationContext()
    with error_context("api.schema", timer=operation.timer):
        try:
            schema = schema_for(name)
        except ValueError as exc:
            if not is_json_mode():
                emit_json(
                    command_envelope(
                        command="api.schema",
                        summary=str(exc),
                        status="failed",
                        exit_code=2,
                        errors=[
                            ErrorModel(
                                code="MERIDIAN_USER_ERROR",
                                category="user",
                                message=str(exc),
                                hint="Run: meridian api schemas",
                                retryable=False,
                                exit_code=2,
                            )
                        ],
                        timer=operation.timer,
                    )
                )
                raise typer.Exit(code=2) from exc
            fail(str(exc), hint="Run: meridian api schemas", hint_type="user")

        if is_json_mode():
            emit_json(
                command_envelope(
                    command="api.schema",
                    data={"name": name, "schema": schema},
                    summary=Summary(text=f"Schema: {name}", changed=False, counts={"schemas": 1}),
                    timer=operation.timer,
                )
            )
            return

        emit_json(schema)


def run_workflow(name: str) -> None:
    """Print one meridian-core workflow plan."""
    operation = OperationContext()
    with error_context("api.workflow", timer=operation.timer):
        try:
            workflow = collect_workflow(name)
        except WorkflowNotFoundError as exc:
            fail(str(exc), hint="Run: meridian api workflow deploy --json", hint_type="user")

        if is_json_mode():
            emit_json(
                command_envelope(
                    command="api.workflow",
                    data={"name": name, "workflow": workflow.model_dump(mode="json")},
                    summary=Summary(text=f"Workflow: {name}", changed=False, counts={"fields": len(workflow.fields)}),
                    timer=operation.timer,
                )
            )
            return

        err_console.print()
        err_console.print(f"  [bold]Meridian workflow: {name}[/bold]")
        for section in workflow.sections:
            err_console.print(f"    [cyan]{section.title}[/cyan]")
            for field_id in section.field_ids:
                field = next((item for item in workflow.fields if item.id == field_id), None)
                if field:
                    required = " required" if field.required else ""
                    err_console.print(f"      {field.id}  [dim]{field.kind}{required}[/dim]")
        err_console.print()
