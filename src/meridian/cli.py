"""Meridian CLI — main Typer application."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Annotated, NoReturn

import typer
from typer.core import TyperGroup

from meridian import __version__
from meridian import cli_types as click
from meridian.cli_catalog import command_path, requests_machine_output, supports_json
from meridian.cli_groups import api_app, client_app, dev_app, fleet_app, node_app, relay_app, route_app, server_app
from meridian.cli_options import (
    JSON_HELP,
    SELF_STEAL_HELP,
    SSH_PORT_HELP,
    USER_HELP,
    YES_HELP,
    Json,
    SshPort,
    Target,
    Timeout,
    User,
    Yes,
    enable_json_output,
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
    (GET_STARTED, ("setup", "deploy", "help")),
    (CLIENTS, ("client",)),
    (SERVERS, ("node", "relay", "route", "fleet", "server")),
    (CHECK_AND_FIX, ("check", "test", "probe", "doctor", "logs", "plan", "apply")),
    (ADVANCED, ("panel", "scan", "studio", "update", "teardown", "api")),
)
# Option panels inside `deploy --help`.
SERVER_ACCESS = "Server access"
METHODS = "Methods"
AUTOMATION = "Automation"
_HELP_ORDER = [name for _panel, names in _HELP_LAYOUT for name in names]


# Old spellings refused with a pointer to the new one, so scripts fail loudly instead of
# hitting Click's bare "No such command".
RENAMED: dict[str, str] = {
    "preflight": "check",
    "rage": "doctor",
    "node check": "check",
    "relay check": "check",
    "relay deploy": "relay add",
}
RENAMED_OPTIONS: dict[str, str] = {"--server": "the first argument"}


@dataclass(frozen=True)
class _Invocation:
    """The command an argv names, resolved through Click before the root callback runs."""

    name: str
    supports_json: bool
    machine_output: bool
    help_requested: bool


_INVOCATION = "meridian.invocation"


class _SectionedGroup(TyperGroup):
    """Lists commands in the `_HELP_LAYOUT` order; anything unlisted (the hidden `dev` app) goes last.

    Resolving a command also records what it is (`_Invocation`) for the root callback,
    which runs before Click parses the command's own options, and refuses `RENAMED` spellings.
    """

    def list_commands(self, ctx: click.Context) -> list[str]:
        names = super().list_commands(ctx)
        return sorted(names, key=lambda name: _HELP_ORDER.index(name) if name in _HELP_ORDER else len(_HELP_ORDER))

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        if not ctx.resilient_parsing:
            path, command, rest = command_path(ctx, args)
            _refuse_renamed(ctx, path, command, rest)
            leaf = command if command is not None and not isinstance(command, click.Group) else None
            ctx.meta[_INVOCATION] = _Invocation(
                name=".".join(path),
                supports_json=leaf is not None and supports_json(leaf),
                machine_output=leaf is not None and requests_machine_output(ctx, path[-1], leaf, rest),
                help_requested=any(arg in ctx.help_option_names for arg in args),
            )
        return super().resolve_command(ctx, args)


def _refuse_renamed(ctx: click.Context, path: list[str], command: click.Command | None, rest: list[str]) -> None:
    """Fail with exit 2 and a one-line hint when the argv uses a spelling from `RENAMED`."""
    old = " ".join(path)
    if old in RENAMED:
        _fail_renamed(ctx, old, f"`meridian {old}` is now `meridian {RENAMED[old]}`", rest)
    if command is None or isinstance(command, click.Group):
        return
    for arg in rest:
        if arg == "--":
            return
        option = arg.split("=", 1)[0]
        if option in RENAMED_OPTIONS:
            message = f"`{option}` is now {RENAMED_OPTIONS[option]}: `meridian {old} SERVER`"
            _fail_renamed(ctx, old, message, rest)


def _fail_renamed(ctx: click.Context, command: str, message: str, rest: list[str]) -> NoReturn:
    from meridian.console import error_context, fail, set_json_mode

    if ctx.params.get("json_mode") or "--json" in rest:
        set_json_mode(True)
    with error_context(command.replace(" ", ".")):
        fail(message, hint_type="user")


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
    epilog=(
        "[dim]Docs: https://getmeridian.org/docs  ·  Help for any command: meridian help COMMAND  ·  "
        "For AI agents: meridian help agents[/dim]"
    ),
    add_completion=True,
    rich_markup_mode="rich",
)

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

    invocation: _Invocation | None = ctx.meta.get(_INVOCATION)
    # `api schema NAME` prints raw JSON Schema even without a flag, so every api command is machine output.
    machine_output = (
        json_mode or ctx.invoked_subcommand == "api" or (invocation is not None and invocation.machine_output)
    )
    if machine_output:
        from meridian.console import set_quiet_mode

        set_quiet_mode(True)

    if json_mode:
        from meridian.console import error_context, fail, set_json_mode

        set_json_mode(True)
        if invocation is None or not invocation.supports_json:
            command = invocation.name if invocation is not None else "cli"
            with error_context(command):
                fail(
                    f"JSON output is not yet supported for `{command.replace('.', ' ')}`",
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

    if not machine_output:
        from meridian import i18n

        # Human output speaks the chosen language, else the terminal's; machine output stays English.
        i18n.set_locale(i18n.explicit_locale() or i18n.system_locale())
    if not machine_output and not (invocation is not None and invocation.help_requested):
        banner(__version__)

    if not DISABLE_UPDATE_CHECK and not machine_output and ctx.invoked_subcommand not in ("update",):
        from meridian.update import check_for_update

        check_for_update(__version__)


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


@app.command("studio", rich_help_panel=ADVANCED)
def studio_cmd(
    port: int | None = typer.Option(
        None,
        "--port",
        min=0,
        max=65535,
        show_default=False,
        help="Localhost port (default 7391, or the next free one, so browser drafts survive restarts); 0 picks any",
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


@app.command("help", rich_help_panel=GET_STARTED)
def help_cmd(
    ctx: typer.Context,
    topic: Annotated[
        list[str] | None,
        typer.Argument(help="'agents' for the AI agent guide, or a command such as 'node add'", show_default=False),
    ] = None,
) -> None:
    """Explain Meridian to an AI agent, or show one command's help

    [dim]Examples:[/dim]
      [cyan]meridian help agents[/cyan]     How Meridian works and how to drive it, for AI agents
      [cyan]meridian help node add[/cyan]   Same as meridian node add --help
    """
    from meridian.commands.help import run

    run(ctx, topic or [])


@app.command("plan", rich_help_panel=CHECK_AND_FIX)
def plan_cmd(
    _json: Json = False,
) -> None:
    """Show what apply would change, comparing your topology with live servers

    [dim]Exit codes:[/dim]
      [green]0[/green] = already converged
      [yellow]1[/yellow] = nothing to plan (no fleet or unusable local state)
      [yellow]2[/yellow] = changes pending
      [yellow]3[/yellow] = required observation evidence unavailable
    """
    from meridian.commands.plan import run

    run()


@app.command("apply", rich_help_panel=CHECK_AND_FIX)
def apply_cmd(
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Apply the reviewed topology in cluster.yml to your servers

    Shows the compiled changes, asks for confirmation, then applies them
    with checkpoints; an interrupted apply resumes where it stopped.

    [dim]Examples:[/dim]
      [cyan]meridian apply[/cyan]              Show changes and confirm
      [cyan]meridian apply --yes[/cyan]        Apply without confirmation
    """
    from meridian.commands.apply import run

    run(yes=yes)


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
    yes: Yes = False,
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
        help="Your domain: XHTTP moves onto it and WSS is added, both through Cloudflare's CDN",
    ),
    sni: str = typer.Option(
        "",
        "--sni",
        "-s",
        help="Camouflage site the server imitates (default: auto-picked; try 'meridian scan')",
    ),
    self_steal: bool = typer.Option(False, "--self-steal", help=SELF_STEAL_HELP),
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
    _json: bool = typer.Option(
        False, "--json", help=JSON_HELP, callback=enable_json_output, rich_help_panel=AUTOMATION
    ),
    events: str = typer.Option(
        "",
        "--events",
        help="Stream progress events as JSONL (use: jsonl)",
        callback=enable_json_output,
        rich_help_panel=AUTOMATION,
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
      [cyan]meridian deploy 198.51.100.10 --domain vpn.example --self-steal[/cyan]  Your domain as camouflage
      [cyan]meridian deploy ubuntu@198.51.100.10:2222[/cyan]  Non-root user, custom SSH port
      [cyan]meridian deploy local[/cyan]                    On this server, without SSH
      [cyan]meridian deploy 198.51.100.10 --no-harden[/cyan]  Skip SSH and firewall hardening
      [cyan]meridian deploy --request deploy.json --json --events=jsonl[/cyan]
    """
    from meridian.commands.deploy import run
    from meridian.console import is_json_mode

    run(
        ip,
        domain,
        sni,
        client_name,
        user,
        yes,
        harden,
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
        self_steal=self_steal,
        request_path=request_path,
        json_output=is_json_mode(),
        events=events,
        dry_run=dry_run,
        cancel_file=cancel_file,
    )


@app.command("check", rich_help_panel=CHECK_AND_FIX)
def check_cmd(
    target: Target = "",
    domain: str = typer.Option("", "--domain", "-d", help="Domain to check on a server not deployed yet"),
    sni: str = typer.Option("", "--sni", "-s", help="Camouflage target to check on a server not deployed yet"),
    user: User = "",
    ssh_port: SshPort = 0,
    ai: bool = typer.Option(False, "--ai", help="Copy an AI-ready diagnostic prompt to the clipboard"),
    _json: Json = False,
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

    run(target, user=user, ssh_port=ssh_port, sni=sni, domain=domain, ai=ai)


@app.command("scan", rich_help_panel=ADVANCED)
def scan_cmd(
    ip: Target = "",
    user: User = "",
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

    run(ip, user, ssh_port=ssh_port)


@app.command("test", rich_help_panel=CHECK_AND_FIX)
def test_cmd(
    ip: str = typer.Argument("", metavar="TARGET", help="Server IP, domain, or saved name", show_default=False),
    domain: str = typer.Option("", "--domain", "-d", help="Domain to test"),
    sni: str = typer.Option("", "--sni", "-s", help="SNI target to test"),
    client: str = typer.Option("", "--client", help="Active client whose connection to test"),
    basic: bool = typer.Option(False, "--basic", help="Run reachability checks without downloading or running Xray"),
    probe: bool = typer.Option(False, "--probe", help="Look at the server the way a censor's scanner would"),
    timeout: Timeout = "5",
    _json: Json = False,
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
    if probe:
        from meridian.commands.probe import run as run_probe
        from meridian.console import fail

        if basic or client or domain:
            fail("--probe cannot be combined with --basic, --client, or --domain", hint_type="user")
        run_probe(ip, sni, timeout)
        return
    from meridian.commands.test import run

    run(ip, domain, sni, client, basic, timeout)


@app.command("probe", rich_help_panel=CHECK_AND_FIX)
def probe_cmd(
    ip: str = typer.Argument("", metavar="TARGET", help="Server IP, domain, or saved name", show_default=False),
    sni: str = typer.Option("", "--sni", "-s", help="SNI to use for generic probes"),
    timeout: Timeout = "5",
    _json: Json = False,
) -> None:
    """Look at a server the way a censor's scanner would (same as test --probe)

    [dim]Examples:[/dim]
      [cyan]meridian probe 198.51.100.10[/cyan]
      [cyan]meridian probe vpn.example --json[/cyan]
    """
    from meridian.commands.probe import run

    run(ip, sni, timeout)


@app.command("doctor", rich_help_panel=CHECK_AND_FIX)
def doctor_cmd(
    ip: Target = "",
    sni: str = typer.Option("", "--sni", "-s", help="SNI target"),
    user: User = "",
    ai: bool = typer.Option(False, "--ai", help="Copy an AI-ready diagnostic prompt to the clipboard"),
    ssh_port: SshPort = 0,
) -> None:
    """Collect a redacted diagnostic report for bug reports

    [dim]Examples:[/dim]
      [cyan]meridian doctor[/cyan]                     Report on the saved server
      [cyan]meridian doctor 198.51.100.10 --ai[/cyan]  Copy an AI-ready report to the clipboard
    """
    from meridian.commands.diagnostics import run

    run(ip, sni, user, ai, ssh_port=ssh_port)


@app.command("logs", rich_help_panel=CHECK_AND_FIX)
def logs_cmd(
    ip: Target = "",
    lines: int = typer.Option(100, "--lines", "-n", min=1, max=10_000, help="Lines to show from each log"),
    pattern: str = typer.Option(
        "", "--grep", "-g", metavar="TEXT", help="Only lines containing this text (case-insensitive, not a regex)"
    ),
    component: list[str] = typer.Option(
        [],
        "--component",
        "-c",
        metavar="NAME",
        help="Only this component: node, panel, nginx, or relay (repeatable)",
        show_default=False,
    ),
    user: User = "",
    ssh_port: SshPort = 0,
) -> None:
    """Show a server's recent node, panel, nginx, and relay logs, redacted

    Reads the logs of what the server's fleet roles run, over SSH, and masks
    secrets, panel and connection-page paths, tokens, and UUIDs.

    [dim]Examples:[/dim]
      [cyan]meridian logs 198.51.100.10[/cyan]                 Last 100 lines of each log
      [cyan]meridian logs exit-de --grep error -n 50[/cyan]    Last 50 lines mentioning "error"
      [cyan]meridian logs 198.51.100.10 -c nginx[/cyan]        Only the nginx error log
    """
    from meridian.commands.logs import run

    run(ip, user=user, ssh_port=ssh_port, lines=lines, pattern=pattern, components=component)


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

    run(ip, user, yes, ssh_port=ssh_port)


@app.command("update", rich_help_panel=ADVANCED)
def update_cmd(
    major: bool = typer.Option(False, "--major", help="Also move to a newer major version"),
) -> None:
    """Update the meridian CLI to the latest release of its major version"""
    from meridian.update import run_self_update

    exit_code = run_self_update(major=major)
    if exit_code:
        raise typer.Exit(exit_code)
