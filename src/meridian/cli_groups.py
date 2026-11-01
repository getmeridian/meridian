"""Typer sub-apps of the `meridian` CLI: client, server, node, relay, route, fleet, api, and dev.

`meridian.cli` mounts them on the root app; each command defers its imports so `--help` stays fast.
"""

from __future__ import annotations

from typing import Annotated

import typer

from meridian.cli_client import client_app as client_app
from meridian.cli_fleet_contact import fleet_contact_cmd
from meridian.cli_fleet_language import fleet_language_cmd
from meridian.cli_fleet_notice import notice_app
from meridian.cli_fleet_routing import fleet_routing_cmd
from meridian.cli_fleet_usage import fleet_usage_cmd
from meridian.cli_node_commands import node_restart_cmd
from meridian.cli_options import (
    ForgetUnreachable,
    Json,
    NewServer,
    SelfSteal,
    SshPort,
    Target,
    User,
    Yes,
    enable_json_output,
)
from meridian.cli_server_metadata import server_set_cmd

server_app = typer.Typer(help="Save servers and review bandwidth allowances and payment dates", no_args_is_help=True)
node_app = typer.Typer(
    help="Add, list, restart, replace, or remove exit servers, or change their SNI", no_args_is_help=True
)
relay_app = typer.Typer(
    help="Add, list, or remove relays that forward traffic to an exit, or change their SNI", no_args_is_help=True
)
route_app = typer.Typer(help="Send chosen destinations through WARP, directly, or nowhere", no_args_is_help=True)
fleet_app = typer.Typer(
    help="Fleet status, inventory, client routing, upgrades, and the fleet as a file", no_args_is_help=True
)
api_app = typer.Typer(help="Machine-readable contracts for scripts and UIs", no_args_is_help=True)
dev_app = typer.Typer(help="Developer tools for testing and debugging", no_args_is_help=True)
fleet_app.command("usage")(fleet_usage_cmd)
node_app.command("restart", rich_help_panel="Operations")(node_restart_cmd)
fleet_app.command("routing", rich_help_panel="Routing")(fleet_routing_cmd)
fleet_app.command("contact", rich_help_panel="Connection pages")(fleet_contact_cmd)
fleet_app.add_typer(notice_app, name="notice", rich_help_panel="Connection pages")
fleet_app.command("language", rich_help_panel="Connection pages")(fleet_language_cmd)
server_app.command("set", rich_help_panel="Allowances and payments")(server_set_cmd)


@server_app.command("add")
def server_add_cmd(
    ip: NewServer,
    name: str = typer.Option("", "--name", help="Display name"),
    user: User = "",
    ssh_port: SshPort = 0,
) -> None:
    """Save a server you reach over SSH"""
    from meridian.commands.server import run_add

    run_add(ip, name, user, ssh_port=ssh_port)


@server_app.command("list")
def server_list_cmd(_json: Json = False) -> None:
    """List saved servers with reviewed allowances and payment dates"""
    from meridian.commands.server import run_list

    run_list()


@server_app.command("remove")
def server_remove_cmd(
    server: str = typer.Argument(..., help="Server name or IP"),
    yes: Yes = False,
) -> None:
    """Remove a saved server"""
    from meridian.commands.server import run_remove

    run_remove(server, yes=yes)


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


@relay_app.command("list")
def relay_list_cmd(
    exit: str = typer.Option("", "--exit", "-e", help="Filter by exit server IP or name"),
    _json: Json = False,
) -> None:
    """List all relay nodes"""
    from meridian.commands.relay import run_list

    run_list(exit)


@relay_app.command("remove")
def relay_remove_cmd(
    relay_ip: str = typer.Argument(..., help="Relay server IP, saved name, or relay chain ID"),
    exit: str = typer.Option("", "--exit", "-e", help="Only chains toward this exit (IP, name, or exit ID)"),
    yes: Yes = False,
    forget_unreachable: ForgetUnreachable = False,
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

    run_remove(relay_ip, exit, yes, forget_unreachable=forget_unreachable)


@relay_app.command("sni")
def relay_sni_cmd(
    relay: str = typer.Argument(..., metavar="RELAY", help="Relay server IP, saved name, or relay chain ID"),
    sni: str = typer.Argument(..., metavar="NEW_SNI", help="New Reality camouflage site"),
    exit: str = typer.Option("", "--exit", "-e", help="Only chains toward this exit (IP, name, or exit ID)"),
    yes: Yes = False,
) -> None:
    """Change the camouflage SNI a relay announces

    Checks that the exit reaches the site over TLS 1.3 with HTTP/2, shows the
    review, confirms, and applies it. Apps switch at their next subscription
    refresh; the old SNI stops working once the change applies. Give the
    exit's own SNI to make the relay follow the exit again.

    [dim]Examples:[/dim]
      [cyan]meridian relay sni 203.0.113.10 dl.google.com[/cyan]
      [cyan]meridian relay sni ru-moscow www.cloudflare.com --yes[/cyan]
    """
    from meridian.commands.sni import run_relay_sni

    run_relay_sni(relay, sni, exit_arg=exit, yes=yes)


@route_app.command("add")
def route_add_cmd(
    exit: str = typer.Argument(..., help="Exit ID, saved server name, or IP"),
    values: list[str] | None = typer.Argument(
        None, metavar="VALUE", help="Domains, geosite:NAME, or geoip:NAME (one kind per route)"
    ),
    via: str = typer.Option("", "--via", help="warp, direct, or block (default for --preset: warp)"),
    preset: str = typer.Option("", "--preset", help="A named destination set instead of VALUEs: ai"),
    name: str = typer.Option("", "--name", help="Route ID (default: the preset, or the --via value, numbered)"),
    _json: Json = False,
) -> None:
    """Send matching destinations through WARP, directly, or nowhere

    Overrides apply on the exit, ahead of its default egress, in the order
    they were added. Using --via warp sets up WARP on the exit if needed.
    --preset ai sends AI services (ChatGPT, Claude, Gemini, Cursor, ...)
    through WARP instead of the exit's hosting address, which some refuse.

    [dim]Examples:[/dim]
      [cyan]meridian route add exit-1 --preset ai[/cyan]
      [cyan]meridian route add exit-1 --via warp netflix.com chatgpt.com[/cyan]
      [cyan]meridian route add exit-1 --via block geosite:category-ads-all[/cyan]
    """
    from meridian.commands.route import run_add

    run_add(exit, values or [], via, name, preset=preset)


@route_app.command("list")
def route_list_cmd(
    exit: str = typer.Argument("", help="Only this exit (ID, saved name, or IP)"),
    _json: Json = False,
) -> None:
    """List every exit's egress overrides in first-match order"""
    from meridian.commands.route import run_list

    run_list(exit)


@route_app.command("remove")
def route_remove_cmd(
    exit: str = typer.Argument(..., help="Exit ID, saved server name, or IP"),
    route_id: str = typer.Argument(..., metavar="ROUTE", help="Route ID from `meridian route list`"),
    yes: Yes = False,
    _json: Json = False,
) -> None:
    """Remove one egress override; its destinations follow the exit's default again"""
    from meridian.commands.route import run_remove

    run_remove(exit, route_id, yes=yes)


@node_app.command("add")
def node_add_cmd(
    ip: NewServer,
    name: str = typer.Option("", "--name", help="Friendly name for the node"),
    user: User = "",
    sni: str = typer.Option("", "--sni", help="Reality SNI target"),
    domain: str = typer.Option(
        "", "--domain", help="Exit's domain: XHTTP moves onto it and WSS is added, both through Cloudflare's CDN"
    ),
    country: str = typer.Option(
        "", "--country", help="Country code shown in connection names (default: the address registry's)"
    ),
    ssh_port: SshPort = 0,
    harden: bool = typer.Option(True, help="Apply OS hardening (fleet servers require it)"),
    self_steal: SelfSteal = False,
    yes: Yes = False,
) -> None:
    """Add an exit server to the fleet

    Connects via SSH, adds the server as an exit in your topology, and
    applies the reviewed plan (hardening, firewall, the node agent and Xray).

    [dim]Examples:[/dim]
      [cyan]meridian node add 198.51.100.10[/cyan]
      [cyan]meridian node add 198.51.100.10 --name finland --sni www.google.com[/cyan]
      [cyan]meridian node add 198.51.100.10 --domain vpn.example.com --yes[/cyan]
      [cyan]meridian node add 198.51.100.10 --sni de.example.com --self-steal[/cyan]   Your own domain
      [cyan]meridian node add 198.51.100.10 --country NL[/cyan]   Registry says otherwise
    """
    from meridian.commands.node import run_add

    run_add(
        ip,
        name=name,
        user=user,
        ssh_port=ssh_port,
        sni=sni,
        domain=domain,
        country=country,
        harden=harden,
        yes=yes,
        self_steal=self_steal,
    )


@node_app.command("list")
def node_list_cmd(
    _json: Json = False,
) -> None:
    """List all nodes with live status"""
    from meridian.commands.node import run_list

    run_list()


@node_app.command("replace")
def node_replace_cmd(
    old: Annotated[str, typer.Argument(metavar="EXIT", help="Exit ID, or the saved name/IP of the exit's server")],
    new: Target,
    name: str = typer.Option("", "--name", help="Friendly name for the new server"),
    user: User = "",
    ssh_port: SshPort = 0,
    yes: Yes = False,
    forget_unreachable: ForgetUnreachable = False,
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

    run_replace(old, new, name=name, user=user, ssh_port=ssh_port, yes=yes, forget_unreachable=forget_unreachable)


@node_app.command("sni")
def node_sni_cmd(
    exit: Annotated[str, typer.Argument(metavar="EXIT", help="Exit ID, or the saved name/IP of the exit's server")],
    sni: Annotated[str, typer.Argument(metavar="NEW_SNI", help="New Reality camouflage site")],
    self_steal: Annotated[
        bool,
        typer.Option(
            "--self-steal",
            help="NEW_SNI is your own domain, pointed at the exit: it gets the certificate and answers probes itself",
        ),
    ] = False,
    yes: Yes = False,
) -> None:
    """Change an exit's camouflage SNI

    Checks that the exit reaches the site over TLS 1.3 with HTTP/2 (with
    --self-steal, that your domain points at the exit), shows the review,
    confirms, and applies it. Relays without their own SNI change with the
    exit. Apps switch at their next subscription refresh; the old SNI stops
    working once the change applies.

    [dim]Examples:[/dim]
      [cyan]meridian node sni finland dl.google.com[/cyan]
      [cyan]meridian node sni 198.51.100.10 www.cloudflare.com --yes[/cyan]
      [cyan]meridian node sni finland www.example.com --self-steal[/cyan]   Your own domain
    """
    from meridian.commands.sni import run_exit_sni

    run_exit_sni(exit, sni, self_steal=self_steal, yes=yes)


@node_app.command("remove")
def node_remove_cmd(
    node: str = typer.Argument(..., help="Node IP or name"),
    yes: Yes = False,
    force: bool = typer.Option(False, "--force", help="Also remove relay chains that route to this exit"),
    forget_unreachable: ForgetUnreachable = False,
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

    run_remove(node, yes=yes, force=force, forget_unreachable=forget_unreachable)


@fleet_app.command("status")
def fleet_status_cmd(
    _json: Json = False,
) -> None:
    """Show fleet health — panel, nodes, relays, clients"""
    from meridian.commands.fleet import run_status

    exit_code = run_status()
    if exit_code:
        raise typer.Exit(exit_code)


@fleet_app.command("inventory")
def fleet_inventory_cmd(
    _json: Json = False,
) -> None:
    """Show configured topology with live status"""
    from meridian.commands.fleet import run_inventory

    run_inventory()


@fleet_app.command("export")
def fleet_export_cmd(
    _json: Json = False,
) -> None:
    """Print the fleet's topology as a YAML file you can edit

    Lists exits, protocol paths, relays, gateways, pools, rules, clients, and
    settings, with saved server names instead of IDs. Reality keys,
    credentials, and transport paths stay in Meridian.

    [dim]Examples:[/dim]
      [cyan]meridian fleet export > fleet.yml[/cyan]
    """
    from meridian.commands.fleet_transfer import run_export

    run_export()


@fleet_app.command("import")
def fleet_import_cmd(
    file: str = typer.Argument(..., metavar="FILE", help="Fleet file from `meridian fleet export`, or - for stdin"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the changes and the review, apply nothing"),
    yes: Yes = False,
    forget_unreachable: ForgetUnreachable = False,
    _json: Json = False,
) -> None:
    """Apply an edited fleet file

    Shows what the file adds, changes, and removes, plus every resource the
    change deletes, then applies exactly that review. Clients removed from the
    file are removed as with `client remove`.

    [dim]Examples:[/dim]
      [cyan]meridian fleet import fleet.yml[/cyan]
      [cyan]meridian fleet import fleet.yml --dry-run[/cyan]
      [cyan]meridian fleet import fleet.yml --yes --json[/cyan]
    """
    from meridian.commands.fleet_transfer import run_import

    run_import(file, dry_run=dry_run, yes=yes, forget_unreachable=forget_unreachable)


@fleet_app.command("history")
def fleet_history_cmd(
    _json: Json = False,
) -> None:
    """List the reviewed applies the control service recorded"""
    from meridian.commands.fleet_history import run_history

    run_history()


@fleet_app.command("events", rich_help_panel="Activity")
def fleet_events_cmd(
    since: str = typer.Option("0", "--since", metavar="ID", help="Read events after this cursor"),
    limit: str = typer.Option("100", "--limit", metavar="N", help="Events to return, 1–2000"),
    _json: Json = False,
) -> None:
    """Read local lifecycle and node outage events, oldest first after the cursor"""
    from meridian.commands.fleet_events import run_events

    run_events(since=since, limit=limit)


@fleet_app.command("upgrade")
def fleet_upgrade_cmd(
    yes: Yes = False,
    tested: bool = typer.Option(
        False,
        "--tested",
        help="Skip GitHub: Realm stays on the tested version or what you already upgraded to",
    ),
) -> None:
    """Bring the control service, node agents, and Realm up to date

    The control service and every node agent move to the versions this
    Meridian installs (run `meridian update` first for newer ones); agents
    upgrade in place and keep their identity. With relays, Realm moves to the
    newest release in the tested major on GitHub, pinned by the sha256 GitHub
    publishes. Shows what each server runs, applies the reviewed plan, then
    runs `meridian check`.

    [dim]Examples:[/dim]
      [cyan]meridian fleet upgrade[/cyan]
      [cyan]meridian fleet upgrade --tested --yes[/cyan]
    """
    from meridian.commands.fleet_upgrade import run_upgrade

    run_upgrade(yes=yes, tested=tested)


@api_app.command("schemas")
def api_schemas_cmd(
    _json: Json = False,
    include_schemas: bool = typer.Option(
        False, "--include-schemas", help="Include full JSON schemas (implies --json)", callback=enable_json_output
    ),
) -> None:
    """List meridian-core JSON schemas"""
    from meridian.commands.api import run_schemas

    run_schemas(include_schemas=include_schemas)


@api_app.command("commands")
def api_commands_cmd(
    _json: Json = False,
    include_schemas: bool = typer.Option(
        False,
        "--include-schemas",
        help="Include envelope and data schemas (implies --json)",
        callback=enable_json_output,
    ),
) -> None:
    """List machine-readable command contracts"""
    from meridian.commands.api import run_commands

    run_commands(include_schemas=include_schemas)


@api_app.command("schema")
def api_schema_cmd(
    name: str = typer.Argument(..., help="Schema name from 'meridian api schemas'"),
    _envelope: bool = typer.Option(
        False, "--envelope", help="Wrap schema in meridian.output/v1", callback=enable_json_output
    ),
    _json: Json = False,
) -> None:
    """Print one meridian-core JSON schema (raw JSON Schema unless --envelope or --json)"""
    from meridian.commands.api import run_schema

    run_schema(name)


@api_app.command("workflow")
def api_workflow_cmd(
    name: str = typer.Argument(..., help="Workflow name (e.g. deploy)"),
    _json: Json = False,
) -> None:
    """Print one meridian-core workflow plan"""
    from meridian.commands.api import run_workflow

    run_workflow(name)


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
