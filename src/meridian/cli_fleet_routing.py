"""Reviewed routing choices and explanations, preserving preset shorthand."""

from __future__ import annotations

import typer

from meridian.cli_options import Json, Yes
from meridian.cli_types import Command, Context, Group


class RoutingGroup(Group):
    """A reviewed name remains shorthand for set; explicit subcommands own their names."""

    def get_command(self, ctx: Context, cmd_name: str) -> Command | None:
        return super().get_command(ctx, cmd_name) or self.commands.get("set")

    def resolve_command(self, ctx: Context, args: list[str]) -> tuple[str | None, Command | None, list[str]]:
        if args and args[0] not in self.commands and not args[0].startswith("-"):
            return args[0], self.commands["set"], args
        return super().resolve_command(ctx, args)


routing_app = typer.Typer(
    cls=RoutingGroup,
    invoke_without_command=True,
    no_args_is_help=False,
    help="Read or review ru-split, full, or named client policies; explain a destination",
)


@routing_app.callback()
def routing_read(
    ctx: Context,
    dry_run: bool = typer.Option(False, "--dry-run", help="Review without applying"),
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Read ru-split, full, or a named fleet policy; POLICY is shorthand for set POLICY."""
    if ctx.invoked_subcommand is None:
        from meridian.commands.fleet_routing import run_routing

        run_routing(dry_run=dry_run, yes=yes)


@routing_app.command("set", rich_help_panel="Policy")
def routing_set(
    ctx: Context,
    policy: str = typer.Argument(..., metavar="POLICY", help="ru-split, full, or a reviewed policy name"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Review the policy change without applying it"),
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Review and apply a named fleet policy; apps receive it on subscription refresh"""
    from meridian.commands.fleet_routing import run_routing

    parent = ctx.parent.params if ctx.parent is not None else {}
    run_routing(
        policy,
        dry_run=dry_run or bool(parent.get("dry_run")),
        yes=yes or bool(parent.get("yes")),
        command="fleet.routing.set" if ctx.info_name == "set" else "fleet.routing",
    )


@routing_app.command("explain", rich_help_panel="Preview")
def routing_explain(
    destination: str = typer.Argument(..., help="Hostname or IP, optionally host:port or udp://host:port"),
    person: str | None = typer.Option(None, "--person", help="Resolve a person's private override and access"),
    document: str = typer.Option("xray-json", "--format", help="xray-json, mihomo, stash, singbox, or base64"),
    port: int | None = typer.Option(None, "--port", help="Destination port when it is absent from the address"),
    network: str | None = typer.Option(None, "--network", help="tcp or udp; defaults to tcp"),
    protocol: str = typer.Option("other", "--protocol", help="other or detected bittorrent"),
    _json: Json = False,
) -> None:
    """Explain the first matching client rule using cached geodata, without network lookups"""
    from meridian.commands.routing_explain import run_explain

    run_explain(destination, person=person, document=document, port=port, network=network, protocol=protocol)
