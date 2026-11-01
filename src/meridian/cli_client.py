"""Client command registration, with validation and work delegated to command modules."""

from __future__ import annotations

import typer

from meridian.cli_client_preview import client_preview_cmd
from meridian.cli_options import CutOff, DeviceLimit, Expires, Json, TrafficLimit, TrafficResetPeriod, Yes

client_app = typer.Typer(
    help="Manage who can connect: add, show, list, limit, reissue, enable, disable, remove", no_args_is_help=True
)
client_app.command("preview", rich_help_panel="Connections")(client_preview_cmd)


@client_app.command("add")
def client_add(
    names: list[str] = typer.Argument(..., metavar="NAME", help="Client name(s) to add"),
    limit: TrafficLimit = None,
    reset: TrafficResetPeriod = None,
    expires: Expires = None,
    devices: DeviceLimit = None,
    _json: Json = False,
    # Adding never asks; accepted so scripts can pass --yes to every mutating command.
    _yes: bool = typer.Option(False, "--yes", "-y", hidden=True),
) -> None:
    """Add one or more clients (people who connect)

    Prints each client's connection page and subscription link. Limits and expiry apply to every name.

    [dim]Examples:[/dim]
      [cyan]meridian client add alice[/cyan]
      [cyan]meridian client add alice bob charlie[/cyan]
      [cyan]meridian client add alice --limit 50GB --reset month --expires 2026-12-31[/cyan]
    """
    from meridian.commands.client import run_add

    run_add(names, limit=limit, reset=reset, expires=expires, devices=devices)


@client_app.command("show")
def client_show_cmd(
    name: str = typer.Argument(..., help="Client name"),
    _json: Json = False,
    connections: bool = typer.Option(
        False, "--connections", help="Show individual connection credentials and format URLs"
    ),
) -> None:
    """Show a client's connection page, subscription link, and status

    [dim]Examples:[/dim]
      [cyan]meridian client show alice[/cyan]
    """
    from meridian.commands.client import run_show

    run_show(name, connections=connections)


@client_app.command("list")
def client_list_cmd(
    _json: Json = False,
    attention: bool = typer.Option(False, "--attention", help="Only people expiring soon, near quota, never used, or without recent traffic"),
    tag: str | None = typer.Option(None, "--tag", metavar="TAG", help="Only people with this tag; none for untagged"),
) -> None:
    """List managed access clients with status and traffic"""
    from meridian.commands.client import run_list

    run_list(attention=attention, tag=tag)


@client_app.command("usage")
def client_usage_cmd(
    name: str = typer.Argument(..., help="Client name"),
    days: str = typer.Option("30", "--days", metavar="N", help="UTC days to show, 1–730, within the retention cap"),
    _json: Json = False,
) -> None:
    """Show a person's daily usage when daily history is on"""
    from meridian.commands.client_usage import run_usage

    run_usage(name, days=days)


@client_app.command("set")
def client_set_cmd(
    name: str = typer.Argument(..., help="Client name"),
    limit: TrafficLimit = None,
    reset: TrafficResetPeriod = None,
    expires: Expires = None,
    devices: DeviceLimit = None,
    note: str | None = typer.Option(
        None, "--note", metavar="TEXT", help='Private note, up to 500 characters; "" clears'
    ),
    tag: str | None = typer.Option(
        None, "--tag", metavar="TAG", help="1–16 letters, digits, underscores (saved in uppercase); none clears"
    ),
    language: str | None = typer.Option(
        None, "--language", metavar="LANGUAGE", help="Page and app language: en, ru, fa, zh; default follows the fleet"
    ),
    _json: Json = False,
) -> None:
    """Change a client's traffic limit, reset period, or expiry

    Only the options you pass change. A client cut off by their limit or expiry can connect
    again as soon as the new values allow it.

    [dim]Examples:[/dim]
      [cyan]meridian client set alice --expires 2026-12-31[/cyan]
      [cyan]meridian client set alice --limit 50GB --reset month[/cyan]
      [cyan]meridian client set alice --limit none --expires never[/cyan]
    """
    from meridian.commands.client_limits import run_set

    run_set(name, limit=limit, reset=reset, expires=expires, devices=devices, note=note, tag=tag, language=language)


@client_app.command("devices", rich_help_panel="Device limits")
def client_devices_cmd(
    name: str = typer.Argument(..., help="Client name"),
    remove: str | None = typer.Option(None, "--remove", metavar="ID", help="Forget one device ID"),
    clear: bool = typer.Option(False, "--clear", help="Forget all of this client's devices"),
    _json: Json = False,
) -> None:
    """Show device counts or details, or forget a device so another can register

    Private mode shows a count; full mode lists devices. Nothing is counted while device limits are off.
    """
    from meridian.commands.client_devices import run_devices

    run_devices(name, remove=remove, clear=clear)


@client_app.command("reset-traffic")
def client_reset_traffic_cmd(
    name: str = typer.Argument(..., help="Client name"),
    _json: Json = False,
) -> None:
    """Start a client's used traffic from zero; a client cut off by their limit connects again

    [dim]Examples:[/dim]
      [cyan]meridian client reset-traffic alice[/cyan]
    """
    from meridian.commands.client_limits import run_reset_traffic

    run_reset_traffic(name)


@client_app.command("extend", rich_help_panel="Limits and expiry")
def client_extend_cmd(
    name: str = typer.Argument(..., help="Client name"),
    days: str = typer.Option(..., "--days", metavar="N", help="Days to add after now or the current expiry, 1–3650"),
    _json: Json = False,
) -> None:
    """Extend a person's access by whole days, including people added in the web panel"""
    from meridian.commands.client_limits import run_extend

    run_extend(name, days=days)


@client_app.command("remove")
def client_remove(
    name: str = typer.Argument(..., help="Client name"),
    yes: Yes = False,
    _json: Json = False,
    cut_off: CutOff = False,
) -> None:
    """Remove a client permanently; their links stop working"""
    from meridian.commands.client import run_remove

    run_remove(name, yes=yes, cut_off=cut_off)


@client_app.command("reissue")
def client_reissue_cmd(
    name: str = typer.Argument(..., help="Client name"),
    yes: Yes = False,
    _json: Json = False,
    cut_off: CutOff = False,
) -> None:
    """Replace a client's links after a leak; the old page and subscription stop working

    Keeps the client and their settings (limits and expiry), and clears registered devices.

    [dim]Examples:[/dim]
      [cyan]meridian client reissue alice[/cyan]
    """
    from meridian.commands.client import run_reissue

    run_reissue(name, yes=yes, cut_off=cut_off)


@client_app.command("enable")
def client_enable_cmd(
    name: str = typer.Argument(..., help="Client name"),
    _json: Json = False,
) -> None:
    """Resume a suspended client"""
    from meridian.commands.client import run_enable

    run_enable(name)


@client_app.command("disable")
def client_disable_cmd(
    name: str = typer.Argument(..., help="Client name"),
    _json: Json = False,
) -> None:
    """Suspend a client until you enable them again"""
    from meridian.commands.client import run_disable

    run_disable(name)
