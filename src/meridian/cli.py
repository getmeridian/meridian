"""Meridian CLI — main Typer application."""

from __future__ import annotations

import sys
from typing import Annotated

import typer
from typer.core import TyperGroup

from meridian import __version__
from meridian.cli_options import (
    SSH_PORT_HELP,
    USER_HELP,
    YES_HELP,
    NewServer,
    ServerAlias,
    SshPort,
    Target,
    Timeout,
    User,
    Yes,
)
from meridian.config import DISABLE_UPDATE_CHECK
from meridian.console import banner

# Top-level `--help` sections, in display order, each with its commands in display order.
# Typer lists plain commands before sub-apps, so the group class below imposes this order.
GET_STARTED = "Get started"
CLIENTS = "Clients"
SERVERS = "Servers"
CHECK_AND_FIX = "Check & fix"
ADVANCED = "Advanced"
_HELP_LAYOUT: tuple[tuple[str, tuple[str, ...]], ...] = (
    (GET_STARTED, ("setup", "deploy")),
    (CLIENTS, ("client",)),
    (SERVERS, ("node", "relay", "route", "fleet", "server")),
    (CHECK_AND_FIX, ("check", "test", "doctor", "plan", "apply")),
    (ADVANCED, ("panel", "scan", "studio", "update", "teardown", "api")),
)
# Option panels inside `deploy --help`.
SERVER_ACCESS = "Server access"
METHODS = "Methods"
AUTOMATION = "Automation"
_HELP_ORDER = [name for _panel, names in _HELP_LAYOUT for name in names]


class _SectionedGroup(TyperGroup):
    """Lists commands in the `_HELP_LAYOUT` order; anything unlisted (hidden aliases) goes last."""

    def list_commands(self, ctx: typer.Context) -> list[str]:  # type: ignore[override]
        names = super().list_commands(ctx)
        return sorted(names, key=lambda name: _HELP_ORDER.index(name) if name in _HELP_ORDER else len(_HELP_ORDER))


app = typer.Typer(
    name="meridian",
    cls=_SectionedGroup,
    help=(
        "Censorship-resistant proxy server management\n\n"
        "Rent a server, run one command, and share a link or QR code.\n\n"
        "[dim]Quick start:[/dim]\n"
        "  [cyan]meridian deploy 198.51.100.10[/cyan]   One server, shareable link at the end\n"
        "  [cyan]meridian setup[/cyan]                  Guided wizard for several servers, relays, or routing\n"
        "  [cyan]meridian client add alice[/cyan]       Give another person access\n"
        "  [cyan]meridian check[/cyan]                  Is everything healthy?"
    ),
    epilog="[dim]Docs: https://getmeridian.org/docs  ·  Help for any command: meridian COMMAND --help[/dim]",
    add_completion=True,
    rich_markup_mode="rich",
)

# Subcommand groups
client_app = typer.Typer(help="Manage who can connect: add, show, list, enable, disable, remove", no_args_is_help=True)
server_app = typer.Typer(help="Save and name servers you reach over SSH", no_args_is_help=True)
node_app = typer.Typer(help="Add, list, replace, or remove exit servers", no_args_is_help=True)
relay_app = typer.Typer(help="Add, list, or remove relays that forward traffic to an exit", no_args_is_help=True)
route_app = typer.Typer(help="Send chosen destinations through WARP, directly, or nowhere", no_args_is_help=True)
fleet_app = typer.Typer(help="Fleet status, inventory, and image upgrades", no_args_is_help=True)
api_app = typer.Typer(help="Machine-readable contracts for scripts and UIs", no_args_is_help=True)
dev_app = typer.Typer(help="Developer tools for testing and debugging", no_args_is_help=True)
app.add_typer(client_app, name="client", rich_help_panel=CLIENTS)
app.add_typer(server_app, name="server", rich_help_panel=SERVERS)
app.add_typer(node_app, name="node", rich_help_panel=SERVERS)
app.add_typer(relay_app, name="relay", rich_help_panel=SERVERS)
app.add_typer(route_app, name="route", rich_help_panel=SERVERS)
app.add_typer(fleet_app, name="fleet", rich_help_panel=SERVERS)
app.add_typer(api_app, name="api", rich_help_panel=ADVANCED)
app.add_typer(dev_app, name="dev", hidden=True)


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", help="Show version and exit"),
    verbose: bool = typer.Option(False, "--verbose", help="Enable debug logging"),
    json_mode: bool = typer.Option(
        False, "--json", help="JSON output to stdout (for scripting; not every command supports it)"
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress progress output"),
    lang: str = typer.Option("", "--lang", help="Language for the setup wizard: en, ru, fa, zh", show_default=False),
) -> None:
    """Meridian — Censorship-resistant proxy server management."""
    if ctx.resilient_parsing:
        return

    if lang:
        from meridian import i18n

        locale = i18n.normalize_locale(lang)
        if locale is None:
            from meridian.console import fail

            fail(f"Unsupported language: {lang}", hint="Use one of: en, ru, fa, zh.", hint_type="user")
        i18n.request_locale(locale)

    if verbose:
        import logging

        logging.basicConfig(
            level=logging.DEBUG,
            format="  %(name)s: %(message)s",
            handlers=[logging.StreamHandler(sys.stderr)],
        )

    machine_output = (
        json_mode
        or ctx.invoked_subcommand == "api"
        or _argv_requests_machine_output()
        or _argv_requests_machine_output(list(ctx.args))
    )
    if machine_output:
        from meridian.console import set_quiet_mode

        set_quiet_mode(True)

    if json_mode:
        from meridian.console import error_context, fail, set_json_mode

        set_json_mode(True)
        candidate_args = [list(ctx.args), sys.argv[1:]]
        if ctx.invoked_subcommand:
            candidate_args.append([ctx.invoked_subcommand])
        if not any(args and _argv_supports_global_json(args) for args in candidate_args):
            command = next((_argv_command_name(args) for args in candidate_args if args), "cli")
            with error_context(command):
                fail(
                    f"JSON output is not yet supported for `{command}`",
                    hint="Run `meridian api commands --json` to list supported machine-readable commands.",
                    hint_type="user",
                )

    if quiet:
        from meridian.console import set_quiet_mode

        set_quiet_mode(True)

    if version:
        print(f"meridian {__version__}")
        raise typer.Exit()

    if ctx.invoked_subcommand is None:
        _offer_setup_or_help(ctx, interactive=not (machine_output or quiet))
        raise typer.Exit()

    # Show banner before subcommands (but not for --help)
    if not machine_output and "--help" not in sys.argv and "-h" not in sys.argv:
        banner(__version__)

    # Auto-update check (skip for meta commands)
    if not DISABLE_UPDATE_CHECK and not machine_output and ctx.invoked_subcommand not in ("update",):
        from meridian.update import check_for_update

        check_for_update(__version__)


def _enable_json_output() -> None:
    """Enable JSON output from command-local --json flags."""
    from meridian.console import set_json_mode, set_quiet_mode

    set_json_mode(True)
    set_quiet_mode(True)


def _offer_setup_or_help(ctx: typer.Context, *, interactive: bool) -> None:
    """Bare `meridian`: in a terminal with no fleet yet, offer the setup wizard; otherwise show help."""
    from meridian.config import CLUSTER_CONFIG

    if interactive and sys.stdin.isatty() and sys.stderr.isatty() and not CLUSTER_CONFIG.exists():
        from meridian import i18n
        from meridian.commands.wizard_prompts import Navigation, Prompter, RichWizardIO

        i18n.set_locale(i18n.explicit_locale() or i18n.system_locale())
        try:
            start = Prompter(RichWizardIO()).yes_no(i18n.t("cli.offer_setup"), default=True)
        except (Navigation, ValueError):
            start = False
        if start:
            from meridian.commands import setup_v4

            setup_v4.run()
            return
    typer.echo(ctx.get_help())


def _argv_requests_machine_output(args: list[str] | None = None) -> bool:
    """Detect command-local machine-output flags before Typer enters subcommands."""
    args = args or sys.argv[1:]
    if "--json" in args or "--envelope" in args:
        return True
    if "--events" in args or any(arg.startswith("--events=") for arg in args):
        return True

    # `meridian api schema NAME` writes raw JSON Schema even without a flag.
    positionals = [arg for arg in args if not arg.startswith("-")]
    return len(positionals) >= 2 and positionals[:2] == ["api", "schema"]


def _argv_positionals(args: list[str] | None = None) -> list[str]:
    """Return argv tokens that identify the command path."""
    args = args or sys.argv[1:]
    positionals: list[str] = []
    skip_next = False
    value_options = {
        "--config",
        "--domain",
        "--server",
        "--sni",
        "--user",
        "--ssh-port",
        "--port",
        "--assets-dir",
        "--parallel",
        "--request",
        "--cancel-file",
        "--events",
        "--timeout",
        "--client",
    }
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in value_options:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        positionals.append(arg)
    return positionals


def _argv_command_name(args: list[str] | None = None) -> str:
    """Best-effort command name for early global JSON validation."""
    positionals = _argv_positionals(args)
    if not positionals:
        return "cli"
    if positionals[0] in {"client", "fleet", "api", "node", "relay"} and len(positionals) >= 2:
        return f"{positionals[0]}.{positionals[1]}"
    return positionals[0]


def _argv_supports_global_json(args: list[str] | None = None) -> bool:
    """Return True when global --json maps to a command envelope."""
    positionals = _argv_positionals(args)
    if not positionals:
        return False
    if positionals[0] in {"plan", "apply", "probe", "test"}:
        return True
    if positionals[0] == "deploy":
        return True
    if positionals[:2] in (
        ["client", "list"],
        ["client", "show"],
        ["client", "add"],
        ["client", "remove"],
        ["client", "enable"],
        ["client", "disable"],
        ["fleet", "status"],
        ["fleet", "inventory"],
        ["node", "list"],
        ["relay", "list"],
        ["relay", "check"],
    ):
        return True
    return positionals[0] == "api"


# =============================================================================
# Studio
# =============================================================================


@app.command("studio", rich_help_panel=ADVANCED)
def studio_cmd(
    port: int = typer.Option(
        0,
        "--port",
        min=0,
        max=65535,
        help="Localhost port; 0 picks an available high port",
    ),
    assets_dir: str = typer.Option("", "--assets-dir", help="Directory containing built Studio assets"),
    no_open: bool = typer.Option(False, "--no-open", help="Print the URL without opening a browser"),
) -> None:
    """Open Meridian Studio, the local web UI (experimental)"""
    try:
        from meridian.commands.studio import run
    except ModuleNotFoundError as exc:
        from meridian.console import fail

        fail(
            f"Meridian Studio needs extra packages ({exc.name})",
            hint="Install them with: uv tool install 'meridian-vpn[studio]'  (or pip install 'meridian-vpn[studio]')",
            hint_type="user",
        )

    run(port=port, assets_dir=assets_dir, no_open=no_open)


# =============================================================================
# Plan / Apply (declarative workflow)
# =============================================================================


@app.command("plan", rich_help_panel=CHECK_AND_FIX)
def plan_cmd(
    json_output: bool = typer.Option(False, "--json", help="Emit plan as JSON for CI consumption"),
) -> None:
    """Show what apply would change, comparing your topology with live servers

    [dim]Exit codes:[/dim]
      [green]0[/green] = already converged
      [yellow]1[/yellow] = nothing to plan (no fleet or unusable local state)
      [yellow]2[/yellow] = changes pending
      [yellow]3[/yellow] = required observation evidence unavailable
    """
    from meridian.commands.plan import run
    from meridian.console import is_json_mode

    if json_output:
        _enable_json_output()
    run(json_output=json_output or is_json_mode())


@app.command("apply", rich_help_panel=CHECK_AND_FIX)
def apply_cmd(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    json_output: bool = typer.Option(False, "--json", help="Emit final apply result as JSON"),
) -> None:
    """Apply the reviewed topology in cluster.yml to your servers

    Shows the compiled changes, asks for confirmation, then applies them
    with checkpoints; an interrupted apply resumes where it stopped.

    [dim]Examples:[/dim]
      [cyan]meridian apply[/cyan]              Show changes and confirm
      [cyan]meridian apply --yes[/cyan]        Apply without confirmation
    """
    from meridian.commands.apply import run
    from meridian.console import is_json_mode

    json_enabled = json_output or is_json_mode()
    if json_enabled:
        _enable_json_output()
    run(yes=yes, json_output=json_enabled)


# =============================================================================
# Setup and deploy
# =============================================================================


@app.command("setup", rich_help_panel=GET_STARTED)
def setup_cmd(
    intent_path: str = typer.Option(
        "",
        "--intent",
        help="Start at review from a complete SetupIntent JSON file, or '-' for stdin",
    ),
    restart: bool = typer.Option(
        False,
        "--restart",
        help="Explicitly discard saved setup progress before starting",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Approve review and apply without confirmation",
    ),
) -> None:
    """Configure one or many servers with a resumable guided wizard

    Asks plain-language questions with safe defaults, shows a review, then
    applies it. Progress is saved, so running it again resumes.
    For one server and no questions, use [cyan]meridian deploy[/cyan].

    [dim]Examples:[/dim]
      [cyan]meridian setup[/cyan]                           Start or resume the wizard
      [cyan]meridian setup --restart[/cyan]                 Discard saved progress and start over
      [cyan]meridian setup --intent intent.json --yes[/cyan]  Apply a prepared topology, no questions
    """
    from meridian.commands.setup_v4 import run

    run(
        intent_path=intent_path,
        restart=restart,
        yes=yes,
    )


@app.command("deploy", rich_help_panel=GET_STARTED)
def deploy_cmd(
    ip: Target = "",
    domain: str = typer.Option(
        "",
        "--domain",
        "-d",
        help="Your domain: adds XHTTP and WSS methods (with Cloudflare CDN fallback)",
    ),
    sni: str = typer.Option(
        "",
        "--sni",
        "-s",
        help="Camouflage site the server imitates (default: auto-picked; try 'meridian scan')",
    ),
    client_name: str = typer.Option("", "--client-name", help="Name for the first client"),
    hysteria2: bool = typer.Option(
        True,
        "--hysteria2/--no-hysteria2",
        help="Offer Hysteria2 over UDP/443 as a last-resort fallback (salamander-obfuscated)",
        rich_help_panel=METHODS,
    ),
    fingerprint: str = typer.Option(
        "",
        "--fingerprint",
        help=(
            "TLS fingerprint clients present: chrome (default), firefox, safari, ios, android, edge, random, randomized"
        ),
        rich_help_panel=METHODS,
    ),
    warp: bool = typer.Option(
        False,
        "--warp/--no-warp",
        help="Route outgoing traffic through Cloudflare WARP",
        rich_help_panel=METHODS,
    ),
    geo_block: bool = typer.Option(
        True,
        "--geo-block/--no-geo-block",
        help="Refuse Russian domains and IPs at the exit; subscriptions already send them directly",
        rich_help_panel=METHODS,
    ),
    user: Annotated[
        str,
        typer.Option("--user", "-u", help=USER_HELP, show_default=False, rich_help_panel=SERVER_ACCESS),
    ] = "",
    ssh_port: Annotated[
        int,
        typer.Option(
            "--ssh-port",
            min=0,
            max=65535,
            help=SSH_PORT_HELP,
            show_default=False,
            rich_help_panel=SERVER_ACCESS,
        ),
    ] = 0,
    harden: bool = typer.Option(
        True,
        "--harden/--no-harden",
        help="Harden SSH and firewall (disable if other services share the server)",
        rich_help_panel=SERVER_ACCESS,
    ),
    client_ips: bool = typer.Option(
        False,
        "--client-ips/--no-client-ips",
        help="Let the panel see each client's real IP (for per-user IP limits); off keeps it private",
    ),
    server: ServerAlias = "",
    display_name: str = typer.Option(
        "",
        "--display-name",
        help="Label for connection pages (e.g. Alice's VPN)",
        rich_help_panel="Branding",
    ),
    country: str = typer.Option(
        "",
        "--country",
        help="Country code shown in connection names, e.g. DE (default: the address registry's country)",
        rich_help_panel="Branding",
    ),
    icon: str = typer.Option("", "--icon", help="Page icon (emoji or image URL)", rich_help_panel="Branding"),
    color: str = typer.Option(
        "", "--color", help="Page color theme (ocean/sunset/forest/lavender/rose/slate)", rich_help_panel="Branding"
    ),
    yes: Annotated[bool, typer.Option("--yes", "-y", help=YES_HELP, rich_help_panel=AUTOMATION)] = False,
    json_output: bool = typer.Option(
        False, "--json", help="Emit final deploy result as JSON", rich_help_panel=AUTOMATION
    ),
    events: str = typer.Option(
        "", "--events", help="Stream progress events as JSONL (use: jsonl)", rich_help_panel=AUTOMATION
    ),
    request_path: str = typer.Option(
        "", "--request", help="Deploy-request JSON file or '-' for stdin", rich_help_panel=AUTOMATION
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Validate and plan without SSH changes", rich_help_panel=AUTOMATION
    ),
    cancel_file: str = typer.Option(
        "",
        "--cancel-file",
        hidden=True,
        help="Stop at the next safe point once this file exists (used by Studio)",
    ),
) -> None:
    """Deploy one server and get a shareable connection link

    Asks for the server IP if none is given. For several servers, relays, or
    routing, use [cyan]meridian setup[/cyan].

    [dim]Examples:[/dim]
      [cyan]meridian deploy[/cyan]                          Ask for the server IP
      [cyan]meridian deploy 198.51.100.10[/cyan]            Deploy with defaults
      [cyan]meridian deploy 198.51.100.10 --domain vpn.example[/cyan]  CDN fallback via Cloudflare
      [cyan]meridian deploy ubuntu@198.51.100.10:2222[/cyan]  Non-root user, custom SSH port
      [cyan]meridian deploy local[/cyan]                    On this server, without SSH
      [cyan]meridian deploy 198.51.100.10 --no-harden[/cyan]  Skip SSH and firewall hardening
      [cyan]meridian deploy --request deploy.json --json --events=jsonl[/cyan]
    """
    from meridian.commands.deploy import run
    from meridian.console import is_json_mode

    json_enabled = json_output or is_json_mode() or bool(events)
    if json_enabled:
        _enable_json_output()
    run(
        ip,
        domain,
        sni,
        client_name,
        user,
        yes,
        harden,
        server,
        server_name=display_name,
        icon=icon,
        color=color,
        warp=warp,
        geo_block=geo_block,
        hysteria2=hysteria2,
        client_ips=client_ips,
        country=country,
        ssh_port=ssh_port,
        fingerprint=fingerprint,
        request_path=request_path,
        json_output=json_enabled,
        events=events,
        dry_run=dry_run,
        cancel_file=cancel_file,
    )


# =============================================================================
# Client
# =============================================================================


@client_app.command("add")
def client_add(
    names: list[str] = typer.Argument(..., help="Client name(s) to add"),
    json_mode: bool = typer.Option(False, "--json", help="Output result as JSON"),
    # Adding never asks; accepted so scripts can pass --yes to every mutating command.
    _yes: bool = typer.Option(False, "--yes", "-y", hidden=True),
) -> None:
    """Add one or more clients (people who connect)

    Prints each client's connection page and subscription link.

    [dim]Examples:[/dim]
      [cyan]meridian client add alice[/cyan]
      [cyan]meridian client add alice bob charlie[/cyan]
    """
    from meridian.commands.client import run_add

    if json_mode:
        _enable_json_output()
    run_add(names, json_mode=json_mode)


@client_app.command("show")
def client_show_cmd(
    name: str = typer.Argument(..., help="Client name"),
    json_mode: bool = typer.Option(False, "--json", help="Output result as JSON"),
    repair_page: bool = typer.Option(False, "--repair-page", help="Republish the client's connection page"),
) -> None:
    """Show a client's connection page, subscription link, and status

    [dim]Examples:[/dim]
      [cyan]meridian client show alice[/cyan]
    """
    from meridian.commands.client import run_show

    if json_mode:
        _enable_json_output()
    run_show(name, repair_page=repair_page)


@client_app.command("list")
def client_list_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Output result as JSON"),
) -> None:
    """List managed access clients with status and traffic"""
    from meridian.commands.client import run_list

    if json_mode:
        _enable_json_output()
    run_list()


@client_app.command("remove")
def client_remove(
    name: str = typer.Argument(..., help="Client name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    json_mode: bool = typer.Option(False, "--json", help="Output result as JSON"),
) -> None:
    """Remove a client permanently; their links stop working"""
    from meridian.commands.client import run_remove

    if json_mode:
        _enable_json_output()
    run_remove(name, yes=yes, json_mode=json_mode)


@client_app.command("enable")
def client_enable_cmd(
    name: str = typer.Argument(..., help="Client name"),
    json_mode: bool = typer.Option(False, "--json", help="Output result as JSON"),
) -> None:
    """Resume a suspended client"""
    from meridian.commands.client import run_enable

    if json_mode:
        _enable_json_output()
    run_enable(name, json_mode=json_mode)


@client_app.command("disable")
def client_disable_cmd(
    name: str = typer.Argument(..., help="Client name"),
    json_mode: bool = typer.Option(False, "--json", help="Output result as JSON"),
) -> None:
    """Suspend a client until you enable them again"""
    from meridian.commands.client import run_disable

    if json_mode:
        _enable_json_output()
    run_disable(name, json_mode=json_mode)


# =============================================================================
# Server
# =============================================================================


@server_app.command("add")
def server_add_cmd(
    ip: NewServer,
    name: str = typer.Option("", "--name", help="Display name"),
    user: User = "",
    ssh_port: SshPort = 0,
) -> None:
    """Add a known server"""
    from meridian.commands.server import run_add

    run_add(ip, name, user, ssh_port=ssh_port)


@server_app.command("list")
def server_list_cmd() -> None:
    """List known servers"""
    from meridian.commands.server import run_list

    run_list()


@server_app.command("remove")
def server_remove_cmd(
    server: str = typer.Argument(..., help="Server name or IP"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Remove a known server"""
    from meridian.commands.server import run_remove

    run_remove(server, yes=yes)


# =============================================================================
# Check / Scan / Test
# =============================================================================


@app.command("check", rich_help_panel=CHECK_AND_FIX)
def check_cmd(
    target: Target = "",
    domain: str = typer.Option("", "--domain", "-d", help="Domain to check on a server not deployed yet"),
    sni: str = typer.Option("", "--sni", "-s", help="Camouflage target to check on a server not deployed yet"),
    user: User = "",
    ssh_port: SshPort = 0,
    ai: bool = typer.Option(False, "--ai", help="Copy an AI-ready diagnostic prompt to the clipboard"),
    server: ServerAlias = "",
    json_output: bool = typer.Option(False, "--json", help="Output a typed check result as JSON"),
) -> None:
    """Check server health, or whether a new server can run Meridian

    A server outside the fleet gets pre-flight checks. Control, exit, and
    gateway servers get panel, container, listener, TLS, and disk checks;
    relays get their hops checked. With no server, the whole fleet is checked.
    Exits 0 when every check passes, 4 on findings, 3 when evidence was unavailable.

    [dim]Examples:[/dim]
      [cyan]meridian check[/cyan]
      [cyan]meridian check 198.51.100.10[/cyan]
      [cyan]meridian check ubuntu@198.51.100.20:2222 --sni www.example.com[/cyan]
    """
    from meridian.commands.health import run

    if json_output:
        _enable_json_output()
    run(target or server, user=user, ssh_port=ssh_port, sni=sni, domain=domain, ai=ai)


# Kept for one release: `check` runs these checks on servers outside the fleet.
@app.command("preflight", hidden=True)
def preflight_cmd(
    ip: Target = "",
    domain: str = typer.Option("", "--domain", "-d", help="Domain to check"),
    sni: str = typer.Option("", "--sni", "-s", help="SNI target to verify"),
    user: User = "",
    ai: bool = typer.Option(False, "--ai", help="Copy diagnostic prompt to clipboard"),
    server: ServerAlias = "",
    ssh_port: SshPort = 0,
) -> None:
    """Validate server compatibility before deploying (use: meridian check)"""
    from meridian.commands.health import run

    run(ip or server, user=user, ssh_port=ssh_port, sni=sni, domain=domain, ai=ai, scope="preflight")


@app.command("scan", rich_help_panel=ADVANCED)
def scan_cmd(
    ip: Target = "",
    user: User = "",
    server: ServerAlias = "",
    ssh_port: SshPort = 0,
) -> None:
    """Find camouflage (SNI) sites that suit your server

    Scans the server's own network with RealiTLScanner (IPv4 only), lets you
    pick a result, and prints the command that uses it.

    [dim]Examples:[/dim]
      [cyan]meridian scan 198.51.100.10[/cyan]
      [cyan]meridian scan ubuntu@198.51.100.10:2222[/cyan]
    """
    from meridian.commands.scan import run

    run(ip, user, server, ssh_port=ssh_port)


@app.command("test", rich_help_panel=CHECK_AND_FIX)
def test_cmd(
    ip: str = typer.Argument("", help="Server IP, domain, or saved name", show_default=False),
    domain: str = typer.Option("", "--domain", "-d", help="Domain to test"),
    sni: str = typer.Option("", "--sni", "-s", help="SNI target to test"),
    server: ServerAlias = "",
    client: str = typer.Option("", "--client", help="Active client used for canonical end-to-end traffic"),
    basic: bool = typer.Option(False, "--basic", help="Run reachability checks without downloading or running Xray"),
    probe: bool = typer.Option(False, "--probe", help="Look at the server the way a censor's scanner would"),
    timeout: Timeout = "5",
    json_output: bool = typer.Option(False, "--json", help="Output a typed verification result as JSON"),
) -> None:
    """Test real proxy traffic from this device (no SSH needed)

    Runs one active client's delivered connection end to end. [cyan]--basic[/cyan]
    only checks reachability and cannot be combined with [cyan]--client[/cyan];
    [cyan]--probe[/cyan] shows what a censor's active prober would see instead.

    [dim]Examples:[/dim]
      [cyan]meridian test[/cyan]                       Test the saved server
      [cyan]meridian test 198.51.100.10 --basic[/cyan]   Reachability only
      [cyan]meridian test --client alice[/cyan]        Use one client's connection
      [cyan]meridian test --probe[/cyan]               See the server as a censor would
    """
    if json_output:
        _enable_json_output()
    if probe:
        from meridian.commands.probe import run as run_probe
        from meridian.console import fail

        if basic or client or domain:
            fail("--probe cannot be combined with --basic, --client, or --domain", hint_type="user")
        run_probe(ip, server, sni, timeout)
        return
    from meridian.commands.test import run

    run(ip, domain, sni, server, client, basic, timeout)


# Kept for one release: now `meridian test --probe`.
@app.command("probe", hidden=True)
def probe_cmd(
    ip: str = typer.Argument("", help="Server IP, domain, or saved name", show_default=False),
    server: ServerAlias = "",
    sni: str = typer.Option("", "--sni", "-s", help="SNI to use for generic probes"),
    timeout: Timeout = "5",
    json_output: bool = typer.Option(False, "--json", help="Output a typed verification result as JSON"),
) -> None:
    """Probe your server as a censor would (use: meridian test --probe)"""
    from meridian.commands.probe import run

    if json_output:
        _enable_json_output()
    run(ip, server, sni, timeout)


# =============================================================================
# Doctor / Teardown / Update
# =============================================================================


@app.command("doctor", rich_help_panel=CHECK_AND_FIX)
def doctor_cmd(
    ip: Target = "",
    sni: str = typer.Option("", "--sni", "-s", help="SNI target"),
    user: User = "",
    ai: bool = typer.Option(False, "--ai", help="Copy diagnostic prompt to clipboard"),
    server: ServerAlias = "",
    ssh_port: SshPort = 0,
) -> None:
    """Collect a redacted diagnostic report for bug reports

    [dim]Examples:[/dim]
      [cyan]meridian doctor[/cyan]                     Report on the saved server
      [cyan]meridian doctor 198.51.100.10 --ai[/cyan]  Copy an AI-ready report to the clipboard
    """
    from meridian.commands.diagnostics import run

    run(ip, sni, user, ai, server, ssh_port=ssh_port)


# Alias: rage → doctor
@app.command("rage", hidden=True)
def rage_cmd(
    ip: Target = "",
    sni: str = typer.Option("", "--sni", "-s", help="SNI target"),
    user: User = "",
    ai: bool = typer.Option(False, "--ai", help="Copy diagnostic prompt to clipboard"),
    server: ServerAlias = "",
    ssh_port: SshPort = 0,
) -> None:
    """Alias for doctor"""
    from meridian.commands.diagnostics import run

    run(ip, sni, user, ai, server, ssh_port=ssh_port)


@app.command("panel", rich_help_panel=ADVANCED)
def panel_cmd(
    show_password: bool = typer.Option(False, "--show-password", help="Also print the admin password"),
) -> None:
    """Show the Remnawave admin panel URL and login, and what Meridian manages there"""
    from meridian.commands.panel import run

    run(show_password=show_password)


@app.command("teardown", rich_help_panel=ADVANCED)
def teardown_cmd(
    ip: Target = "",
    user: User = "",
    yes: Yes = False,
    server: ServerAlias = "",
    ssh_port: SshPort = 0,
) -> None:
    """Remove Meridian from a server

    A single-server deployment is removed completely, including the local
    configuration. In a multi-server fleet, a server that still has a role is
    refused; remove it with [cyan]meridian node remove[/cyan] first.

    [dim]Examples:[/dim]
      [cyan]meridian teardown 198.51.100.10[/cyan]
      [cyan]meridian teardown ubuntu@198.51.100.10:2222[/cyan]
    """
    from meridian.commands.uninstall import run

    run(ip, user, yes, server, ssh_port=ssh_port)


@app.command("update", rich_help_panel=ADVANCED)
def update_cmd(
    major: bool = typer.Option(False, "--major", help="Also move to a newer major version"),
) -> None:
    """Update the meridian CLI to the latest release of its major version"""
    from meridian.update import run_self_update

    exit_code = run_self_update(major=major)
    if exit_code:
        raise typer.Exit(exit_code)


# =============================================================================
# Relay
# =============================================================================


@relay_app.command("add")
def relay_add_cmd(
    relay_ip: NewServer,
    exit: str = typer.Option(..., "--exit", "-e", help="Exit server IP or name"),
    user: User = "",
    name: str = typer.Option("", "--name", help="Friendly name for the relay (e.g. ru-moscow)"),
    port: int = typer.Option(443, "--port", "-p", help="Relay listen port"),
    sni: str = typer.Option(
        "",
        "--sni",
        help="Reality SNI (inherits the exit path's when omitted)",
    ),
    yes: Yes = False,
    ssh_port: SshPort = 0,
) -> None:
    """Add a TCP relay that forwards to an exit

    [dim]Examples:[/dim]
      [cyan]meridian relay add 203.0.113.10 --exit 198.51.100.10[/cyan]
      [cyan]meridian relay add 203.0.113.10 --exit myserver --name ru-moscow[/cyan]
      [cyan]meridian relay add 203.0.113.10 --exit 198.51.100.10 --sni yandex.ru[/cyan]
    """
    from meridian.commands.relay import run_deploy

    run_deploy(relay_ip, exit, user, name, port, yes, sni=sni, ssh_port=ssh_port)


# Earlier name for `relay add`; kept so existing scripts keep working.
relay_app.command("deploy", hidden=True)(relay_add_cmd)


@relay_app.command("list")
def relay_list_cmd(
    exit: str = typer.Option("", "--exit", "-e", help="Filter by exit server IP or name"),
    json_mode: bool = typer.Option(False, "--json", help="Output result as JSON"),
) -> None:
    """List all relay nodes"""
    from meridian.commands.relay import run_list

    if json_mode:
        _enable_json_output()
    run_list(exit)


@relay_app.command("remove")
def relay_remove_cmd(
    relay_ip: str = typer.Argument(..., help="Relay server IP, saved name, or relay chain ID"),
    exit: str = typer.Option("", "--exit", "-e", help="Only chains toward this exit (IP, name, or exit ID)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Remove relay chains through a relay server

    Drops every chain that uses the server (or one chain by ID), shows what
    will be deleted, and applies the reviewed removal: Realm units, firewall
    rules, the exit-side listener, and the published Hosts. The server stays
    in your saved servers.

    [dim]Examples:[/dim]
      [cyan]meridian relay remove 198.51.100.20[/cyan]
      [cyan]meridian relay remove relay-chain --yes[/cyan]
    """
    from meridian.commands.relay import run_remove

    run_remove(relay_ip, exit, yes)


# Kept for one release: `meridian check RELAY` checks the same hops.
@relay_app.command("check", hidden=True)
def relay_check_cmd(
    relay_ip: str = typer.Argument(..., help="Relay server IP, saved name, or relay chain ID"),
    exit: str = typer.Option("", "--exit", "-e", help="Only chains toward this exit (IP, name, or exit ID)"),
    user: User = "",
    json_mode: bool = typer.Option(False, "--json", help="Output result as JSON"),
) -> None:
    """Check relay hops: Realm unit, next-hop TCP, and the public entry.

    Exits 0 when every check passes, 4 on negative findings, and 3 when
    a hop could not be inspected.

    [dim]Examples:[/dim]
      [cyan]meridian relay check 198.51.100.20[/cyan]
      [cyan]meridian relay check relay-chain --json[/cyan]
    """
    from meridian.commands.relay_check import run_check

    if json_mode:
        _enable_json_output()
    run_check(relay_ip, exit, user)


# =============================================================================
# Route (per-destination egress overrides on exits)
# =============================================================================


@route_app.command("add")
def route_add_cmd(
    exit: str = typer.Argument(..., help="Exit ID, saved server name, or IP"),
    values: list[str] = typer.Argument(..., help="Domains, geosite:NAME, or geoip:NAME (one kind per route)"),
    via: str = typer.Option(..., "--via", help="warp, direct, or block"),
    name: str = typer.Option("", "--name", help="Route ID (default: the --via value, numbered)"),
    json_mode: bool = typer.Option(False, "--json", help="Output result as JSON"),
) -> None:
    """Send matching destinations through WARP, directly, or nowhere

    Overrides apply on the exit, ahead of its default egress, in the order
    they were added. Using --via warp sets up WARP on the exit if needed.

    [dim]Examples:[/dim]
      [cyan]meridian route add exit-1 --via warp netflix.com chatgpt.com[/cyan]
      [cyan]meridian route add exit-1 --via block geosite:category-ads-all[/cyan]
    """
    from meridian.commands.route import run_add

    if json_mode:
        _enable_json_output()
    run_add(exit, values, via, name)


@route_app.command("list")
def route_list_cmd(
    exit: str = typer.Argument("", help="Only this exit (ID, saved name, or IP)"),
    json_mode: bool = typer.Option(False, "--json", help="Output result as JSON"),
) -> None:
    """List every exit's egress overrides in first-match order"""
    from meridian.commands.route import run_list

    if json_mode:
        _enable_json_output()
    run_list(exit)


@route_app.command("remove")
def route_remove_cmd(
    exit: str = typer.Argument(..., help="Exit ID, saved server name, or IP"),
    route_id: str = typer.Argument(..., help="Route ID from `meridian route list`"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    json_mode: bool = typer.Option(False, "--json", help="Output result as JSON"),
) -> None:
    """Remove one egress override; its destinations follow the exit's default again"""
    from meridian.commands.route import run_remove

    if json_mode:
        _enable_json_output()
    run_remove(exit, route_id, yes=yes)


# =============================================================================
# Node (multi-node fleet management)
# =============================================================================


@node_app.command("add")
def node_add_cmd(
    ip: NewServer,
    name: str = typer.Option("", "--name", help="Friendly name for the node"),
    user: User = "",
    sni: str = typer.Option("", "--sni", help="Reality SNI target"),
    domain: str = typer.Option("", "--domain", help="Domain for WSS/XHTTP (Cloudflare CDN)"),
    country: str = typer.Option(
        "", "--country", help="Country code shown in connection names (default: the address registry's)"
    ),
    ssh_port: SshPort = 0,
    harden: bool = typer.Option(True, help="Apply OS hardening (fleet servers require it)"),
    yes: Yes = False,
) -> None:
    """Add an exit server to the fleet

    Connects via SSH, adds the server as an exit in your topology, and
    applies the reviewed plan (hardening, Docker, Xray, panel registration).

    [dim]Examples:[/dim]
      [cyan]meridian node add 198.51.100.10[/cyan]
      [cyan]meridian node add 198.51.100.10 --name finland --sni www.google.com[/cyan]
      [cyan]meridian node add 198.51.100.10 --domain proxy.example.com --yes[/cyan]
      [cyan]meridian node add 198.51.100.10 --country NL[/cyan]   Registry says otherwise
    """
    from meridian.commands.node import run_add

    run_add(
        ip, name=name, user=user, ssh_port=ssh_port, sni=sni, domain=domain, country=country, harden=harden, yes=yes
    )


@node_app.command("list")
def node_list_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Output result as JSON"),
) -> None:
    """List all nodes with live status"""
    from meridian.commands.node import run_list

    if json_mode:
        _enable_json_output()
    run_list()


# Kept for one release: now `meridian check NODE`.
@node_app.command("check", hidden=True)
def node_check_cmd(
    node: str = typer.Argument(..., help="Node IP or name"),
    user: User = "",
) -> None:
    """Run health checks on a node (use: meridian check NODE)"""
    from meridian.commands.health import run

    run(node, user=user, scope="server")


@node_app.command("replace")
def node_replace_cmd(
    old: Annotated[str, typer.Argument(metavar="EXIT", help="Exit ID, or the saved name/IP of the exit's server")],
    new: Target,
    name: str = typer.Option("", "--name", help="Friendly name for the new server"),
    user: User = "",
    ssh_port: SshPort = 0,
    yes: Yes = False,
) -> None:
    """Move an exit to another server

    Keeps the exit's ID, Reality keys, protocol paths, SNI, WARP and geo
    settings, and every relay pointing at it. Shows the review (deploy on
    SERVER, clean up the old one), confirms, and applies it. The control server
    cannot be moved yet.

    [dim]Examples:[/dim]
      [cyan]meridian node replace finland 198.51.100.20[/cyan]
      [cyan]meridian node replace exit-2 admin@198.51.100.21:2222 --name finland-2 --yes[/cyan]
    """
    from meridian.commands.node_replace import run_replace

    run_replace(old, new, name=name, user=user, ssh_port=ssh_port, yes=yes)


@node_app.command("remove")
def node_remove_cmd(
    node: str = typer.Argument(..., help="Node IP or name"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
    force: bool = typer.Option(False, "--force", help="Also remove relay chains that route to this exit"),
) -> None:
    """Remove an exit or routing gateway from the fleet

    Edits the topology, shows every resource that will be deleted (panel
    node, profile, node container, firewall rules, Hosts), and applies the
    reviewed removal. The control server is removed only by teardown. The
    server stays in your saved servers.

    [dim]Examples:[/dim]
      [cyan]meridian node remove 198.51.100.11[/cyan]
      [cyan]meridian node remove finland --force --yes[/cyan]
    """
    from meridian.commands.node import run_remove

    run_remove(node, yes=yes, force=force)


# =============================================================================
# Fleet (health & recovery)
# =============================================================================


@fleet_app.command("status")
def fleet_status_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Output result as JSON"),
) -> None:
    """Show fleet health — panel, nodes, relays, clients"""
    from meridian.commands.fleet import run_status

    if json_mode:
        _enable_json_output()
    exit_code = run_status()
    if exit_code:
        raise typer.Exit(exit_code)


@fleet_app.command("inventory")
def fleet_inventory_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Output result as JSON"),
) -> None:
    """Show configured topology with live status"""
    from meridian.commands.fleet import run_inventory

    if json_mode:
        _enable_json_output()
    run_inventory()


@fleet_app.command("upgrade")
def fleet_upgrade_cmd(
    yes: Yes = False,
    tested: bool = typer.Option(
        False, "--tested", help="Use only the Remnawave versions this Meridian release was tested with"
    ),
) -> None:
    """Upgrade Remnawave on every server to its newest release in the tested major

    Finds the newest backend, node, and subscription page releases on Docker
    Hub within the major this Meridian release supports (no Meridian update
    needed), shows what each server runs, applies the reviewed plan, then runs
    `meridian check`. Newer or floating tags you chose are kept; PostgreSQL
    and Valkey stay on Meridian's pins, and a PostgreSQL major is never moved.

    [dim]Examples:[/dim]
      [cyan]meridian fleet upgrade[/cyan]
      [cyan]meridian fleet upgrade --tested --yes[/cyan]
    """
    from meridian.commands.fleet_upgrade import run_upgrade

    run_upgrade(yes=yes, tested=tested)


# =============================================================================
# API contracts
# =============================================================================


@api_app.command("schemas")
def api_schemas_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Output schema catalog as JSON envelope"),
    include_schemas: bool = typer.Option(False, "--include-schemas", help="Include full JSON schemas in output"),
) -> None:
    """List meridian-core JSON schemas"""
    from meridian.commands.api import run_schemas

    if json_mode or include_schemas:
        _enable_json_output()
    run_schemas(json_output=json_mode or include_schemas, include_schemas=include_schemas)


@api_app.command("commands")
def api_commands_cmd(
    json_mode: bool = typer.Option(False, "--json", help="Output command catalog as JSON envelope"),
    include_schemas: bool = typer.Option(False, "--include-schemas", help="Include envelope and data schemas"),
) -> None:
    """List machine-readable command contracts"""
    from meridian.commands.api import run_commands

    if json_mode or include_schemas:
        _enable_json_output()
    run_commands(json_output=json_mode or include_schemas, include_schemas=include_schemas)


@api_app.command("schema")
def api_schema_cmd(
    name: str = typer.Argument(..., help="Schema name from 'meridian api schemas'"),
    envelope_output: bool = typer.Option(False, "--envelope", help="Wrap schema in meridian.output/v1"),
    json_mode: bool = typer.Option(False, "--json", help="Alias for --envelope"),
) -> None:
    """Print one meridian-core JSON schema"""
    from meridian.commands.api import run_schema

    if envelope_output or json_mode:
        _enable_json_output()
    run_schema(name, envelope_output=envelope_output or json_mode)


@api_app.command("workflow")
def api_workflow_cmd(
    name: str = typer.Argument(..., help="Workflow name (e.g. deploy)"),
    json_mode: bool = typer.Option(False, "--json", help="Output workflow plan as JSON envelope"),
) -> None:
    """Print one meridian-core workflow plan"""
    from meridian.commands.api import run_workflow

    if json_mode:
        _enable_json_output()
    run_workflow(name, json_output=json_mode)


# =============================================================================
# Dev tools
# =============================================================================


@dev_app.command("preview")
def dev_preview_cmd(
    port: int = typer.Option(8787, "--port", "-p", help="Local server port"),
    name: str = typer.Option("demo", "--name", help="Client name for preview"),
    ip: str = typer.Option("198.51.100.1", "--ip", help="Demo server IP"),
    no_open: bool = typer.Option(False, "--no-open", help="Print URL without opening browser"),
    output: str = typer.Option("", "--output", "-o", help="Write files to directory instead of serving"),
    watch: bool = typer.Option(False, "--watch", "-w", help="Watch source files and live-reload on change"),
) -> None:
    """Preview PWA connection page locally (no VPS required)

    Generates a complete connection page with demo data and serves it
    on localhost. All PWA features work: service worker, install prompt,
    offline mode, platform detection.

    [dim]Examples:[/dim]
      [cyan]meridian dev preview[/cyan]                   Launch preview in browser
      [cyan]meridian dev preview --watch[/cyan]            Live-reload on source changes
      [cyan]meridian dev preview --port 9000[/cyan]       Use custom port
      [cyan]meridian dev preview --name alice[/cyan]      Preview with client name
      [cyan]meridian dev preview -o /tmp/pwa[/cyan]       Save files without serving
    """
    from meridian.commands.dev import run_preview

    run_preview(port=port, client_name=name, server_ip=ip, no_open=no_open, output=output, watch=watch)
