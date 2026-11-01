"""Shared Typer option and argument types so every command speaks one flag vocabulary."""

from __future__ import annotations

from typing import Annotated

import typer

from meridian import cli_types as click

TARGET_HELP = "Server: IP, saved name, user@host[:port], or 'local'"

Target = Annotated[str, typer.Argument(metavar="SERVER", help=TARGET_HELP, show_default=False)]
NewServer = Annotated[
    str, typer.Argument(metavar="SERVER", help="Server IP, user@IP[:port], or local (this machine)", show_default=False)
]
USER_HELP = "SSH user (default: saved, else root)"
SSH_PORT_HELP = "SSH port (default: saved, else 22)"
YES_HELP = "Skip confirmation"
User = Annotated[str, typer.Option("--user", "-u", help=USER_HELP, show_default=False)]
SshPort = Annotated[
    int,
    typer.Option("--ssh-port", min=0, max=65535, help=SSH_PORT_HELP, show_default=False),
]
# A string so bad values reach the command's validation and keep the JSON error envelope.
Timeout = Annotated[
    str, typer.Option("--timeout", metavar="SECONDS", help="Per-network-operation timeout, 1-30 seconds")
]
Yes = Annotated[bool, typer.Option("--yes", "-y", help=YES_HELP)]
CutOff = Annotated[
    bool,
    typer.Option(
        "--cut-off",
        help="End every open session on affected inbounds after revocation; other people reconnect. "
        "If the inbound API fails, restart the core. Opt-in; recommended after a leak.",
    ),
]
ForgetUnreachable = Annotated[
    bool,
    typer.Option(
        "--forget-unreachable",
        help="Forget what was deployed on a removed server whose SSH port does not answer (blocked or deleted)",
    ),
]
SELF_STEAL_HELP = (
    "Camouflage with your own domain (--sni, else --domain) instead of someone else's site: the server gets "
    "its certificate and answers probes itself. Point a DNS-only A record at the server first"
)
SelfSteal = Annotated[bool, typer.Option("--self-steal", help=SELF_STEAL_HELP)]
# Strings so bad values reach `core.client_limits` and keep the JSON error envelope.
TrafficLimit = Annotated[
    str | None,
    typer.Option(
        "--limit",
        metavar="SIZE",
        help="Traffic limit, such as 500MB, 50GB, or 1.5TB; none removes it",
        show_default=False,
    ),
]
TrafficResetPeriod = Annotated[
    str | None,
    typer.Option(
        "--reset",
        metavar="PERIOD",
        help="When used traffic starts again from zero: day, week, month, month_rolling (anniversary), or never",
        show_default=False,
    ),
]
Expires = Annotated[
    str | None,
    typer.Option(
        "--expires",
        metavar="DATE",
        help="Cut access off after DATE (YYYY-MM-DD, the end of that day UTC, or an ISO time); never removes it",
        show_default=False,
    ),
]
DeviceLimit = Annotated[
    str | None,
    typer.Option(
        "--devices",
        metavar="N|unlimited|default",
        help="Device cap, 1–100; unlimited exempts; default uses the fleet's cap",
        show_default=False,
    ),
]
ResetAnchorDay = Annotated[
    str | None,
    typer.Option(
        "--reset-day",
        metavar="N|default",
        help="Anniversary reset day, 1–31; default uses the creation day",
        show_default=False,
    ),
]
ActivationDuration = Annotated[
    str | None,
    typer.Option(
        "--duration",
        metavar="DAYS|none",
        help="Valid for 1–3650 days from first traffic; none clears the duration",
        show_default=False,
    ),
]
RenewalDays = Annotated[
    str | None,
    typer.Option(
        "--renew-days",
        metavar="DAYS",
        help="Renew expiry every 1–3650 UTC days and reset period traffic",
        show_default=False,
    ),
]
RenewalCalendarMonths = Annotated[
    str | None,
    typer.Option(
        "--renew-months",
        metavar="MONTHS",
        help="Renew expiry every 1–120 calendar months, preserving its original day",
        show_default=False,
    ),
]
RenewalCap = Annotated[
    str | None,
    typer.Option(
        "--renewals", metavar="N|unlimited", help="Renewal cap, 1–10000; unlimited removes the cap", show_default=False
    ),
]
NoRenewal = Annotated[bool, typer.Option("--no-renew", help="Turn automatic renewal off")]


def enable_json_output(ctx: typer.Context, value: object) -> object:
    """Callback for machine-output flags: switch the console to JSON before the command runs.

    Every option with this callback counts as a machine-output request, so the root
    callback suppresses the banner and the update check for it (see `meridian.cli_catalog`).
    """
    if value and not ctx.resilient_parsing:
        from meridian.console import set_json_mode, set_quiet_mode

        set_json_mode(True)
        set_quiet_mode(True)
    return value


JSON_HELP = "Output the result as a meridian.output/v1 JSON envelope"
Json = Annotated[bool, typer.Option("--json", help=JSON_HELP, callback=enable_json_output)]

CONTEXT_HELP = "Fleet to work on, by context name (default: MERIDIAN_CONTEXT, else the current context)"
# Where a leaf `--context` would only mislead: `context` names its own fleets, `serve` runs on a server.
CONTEXT_FREE = ("context", "serve")
LEAF_CONTEXT = "meridian.leaf_context"
GROUP_CONTEXT = "meridian.group_context"
GROUP_MACHINE_OUTPUT = "meridian.group_machine_output"


def _remember_leaf_context(ctx: click.Context, _param: object, value: object) -> object:
    if value:
        ctx.meta[LEAF_CONTEXT] = value
    return value


def add_context_option(group: click.Group) -> None:
    """Give every leaf below ``group`` a `--context NAME` option, so it also works after the command.

    The root callback declares the same option and selects the fleet once, before any command
    runs (`meridian.cli_catalog.requested_context` reads a leaf's value early); the leaf option
    reaches no command function.
    """
    for name, command in group.commands.items():
        if name in CONTEXT_FREE:
            continue
        if isinstance(command, click.Group):
            add_context_option(command)
            if not command.invoke_without_command:
                continue
        if not any("--context" in getattr(param, "opts", ()) for param in command.params):
            command.params.append(
                click.Option(
                    param_decls=["--context"],
                    metavar="NAME",
                    default="",
                    expose_value=False,
                    callback=_remember_leaf_context,
                    help=CONTEXT_HELP,
                )
            )
