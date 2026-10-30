"""CLI options for reviewed server allowances and payment reminders."""

from __future__ import annotations

import typer

from meridian.cli_options import Json, Yes


def server_set_cmd(
    name: str = typer.Argument(..., metavar="NAME", help="Declared server name, profile ID, or address"),
    allowance: str | None = typer.Option(
        None, "--allowance", metavar="SIZE", help="Monthly bytes: 20TB, 50GiB, or none"
    ),
    reset_day: str | None = typer.Option(
        None, "--reset-day", metavar="DAY", help="UTC reset day, 1–31; short months use their last day"
    ),
    provider: str | None = typer.Option(
        None, "--provider", metavar="TEXT", help="Provider label, up to 80 characters; none clears it"
    ),
    pay_on: str | None = typer.Option(
        None, "--pay-on", metavar="DATE", help="Payment date YYYY-MM-DD; none clears it and its repeat"
    ),
    pay_every: str | None = typer.Option(
        None, "--pay-every", metavar="PERIOD", help="month, year, or none for a one-off payment"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Review metadata without applying it"),
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Review and apply a server's allowance, provider, or payment date

    Omitted options keep their values. Allowances use the client quota parser:
    GB/TB are aliases for GiB/TiB. Accepted upload + download is approximate;
    overhead, the server's own downloads, and relay forwarding are excluded.
    Servers without a traffic agent are not measured.

    [dim]Examples:[/dim]
      [cyan]meridian server set edge --allowance 20TB --reset-day 15 --yes[/cyan]
      [cyan]meridian server set edge --pay-on 2026-11-15 --pay-every month --dry-run --json[/cyan]
      [cyan]meridian server set edge --allowance none --pay-on none --yes[/cyan]
    """
    from meridian.commands.server_set import run_set

    run_set(
        name,
        allowance=allowance,
        reset_day=reset_day,
        provider=provider,
        pay_on=pay_on,
        pay_every=pay_every,
        dry_run=dry_run,
        yes=yes,
    )
