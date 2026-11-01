"""API token commands; parsing stays separate from service access and rendering."""

from typing import Annotated

import typer

from meridian.cli_options import Json, Yes

token_app = typer.Typer(help="Issue and revoke API tokens with explicit permissions", no_args_is_help=True)


@token_app.command("capabilities")
def capabilities_cmd(_json: Json = False) -> None:
    """List available actions and compatible scope presets"""
    from meridian.commands.token import run_capabilities

    run_capabilities()


@token_app.command("list")
def list_cmd(_json: Json = False) -> None:
    """List token metadata without secrets"""
    from meridian.commands.token import run_list

    run_list()


@token_app.command("create")
def create_cmd(
    name: str = typer.Argument(..., help="A short name for the integration or managing device"),
    capability: Annotated[
        list[str] | None, typer.Option("--capability", help="An action from token capabilities; repeat to combine")
    ] = None,
    scope: Annotated[
        str | None,
        typer.Option(help="Compatible read, propose, approve, people, or admin preset instead of capabilities"),
    ] = None,
    expires_days: Annotated[int | None, typer.Option(help="Whole days until expiry, 1–3650; defaults to 90")] = None,
    expires_at: Annotated[str | None, typer.Option(help="Future UTC expiry instead of days")] = None,
    never_expires: Annotated[
        bool, typer.Option("--never-expires", help="Explicitly issue a token without an expiry")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Preview permissions and expiry without issuing a token")
    ] = False,
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Review effective permissions and issue a token shown once"""
    from meridian.commands.token import run_create

    run_create(
        name,
        capabilities=capability,
        scope=scope,
        expires_days=expires_days,
        expires_at=expires_at,
        never_expires=never_expires,
        dry_run=dry_run,
        yes=yes,
    )


@token_app.command("revoke")
def revoke_cmd(
    token_id: str = typer.Argument(..., help="Public token ID from token list"),
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Revoke one token from its next request"""
    from meridian.commands.token import run_revoke

    run_revoke(token_id, yes=yes)
