"""Deploy proxy server — interactive wizard and provisioner execution.

Meridian 4.0: Remnawave panel + node architecture. The provisioner deploys
containers via SSH, then this module calls the Remnawave REST API directly
from the deployer's machine to configure users, profiles, and hosts.
"""

from __future__ import annotations

import re
import shlex
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError

from meridian.adapters import JsonlReporter
from meridian.cluster import (
    BrandingConfig,
    ClusterConfig,
)
from meridian.commands.resolve import (
    ResolvedServer,
    detect_public_ip,
    ensure_server_connection,
    is_local_keyword,
    resolve_server,
)
from meridian.config import (
    DEFAULT_SNI,
    SERVERS_FILE,
    is_ip,
)
from meridian.console import (
    choose,
    confirm,
    err_console,
    error_context,
    fail,
    info,
    is_quiet_mode,
    line,
    ok,
    prompt,
    set_quiet_mode,
    warn,
)
from meridian.core.deploy import (
    DeployRequest,
    DeployResult,
    DeployWorkflowAnswers,
    apply_deploy_workflow_answers,
    build_deploy_workflow,
)
from meridian.core.deploy_planning import (
    DeployPlan,
)
from meridian.core.deploy_validation import DeployValidationError, normalize_deploy_request
from meridian.core.events import COMMAND_COMPLETED, COMMAND_STARTED
from meridian.core.models import OutputStatus, Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.reporters import Reporter, emit_event
from meridian.core.services.deploy import deploy_server
from meridian.core.validation import wrap_validation_error
from meridian.engine.deploy import EngineError, dry_run_deploy_request, plan_deploy_request, resolve_deploy_target
from meridian.remnawave import MeridianPanel, RemnawaveError
from meridian.renderers import emit_json
from meridian.servers import ServerEntry, ServerRegistry
from meridian.ssh import ServerConnection

# ---------------------------------------------------------------------------
# Entry point (called from cli.py as `run`)
# ---------------------------------------------------------------------------


def run(
    ip: str = "",
    domain: str = "",
    sni: str = "",
    client_name: str = "",
    user: str = "root",
    yes: bool = False,
    harden: bool = True,
    requested_server: str = "",
    *,
    server_name: str = "",
    icon: str = "",
    color: str = "",
    decoy: str = "",
    pq: bool = False,
    warp: bool = False,
    geo_block: bool = True,
    ssh_port: int = 22,
    request_path: str = "",
    json_output: bool = False,
    events: str = "",
    dry_run: bool = False,
) -> None:
    """Deploy a VLESS+Reality proxy server (Remnawave architecture)."""
    operation = OperationContext()
    final_json = json_output or events == "jsonl"
    reporter: Reporter | None = JsonlReporter() if events == "jsonl" else None

    with error_context("deploy", timer=operation.timer), _quiet_machine_output(final_json):
        if events and events != "jsonl":
            fail(f"Unsupported --events value: {events}", hint="Use: --events=jsonl", hint_type="user")

        request = (
            _load_deploy_request(request_path)
            if request_path
            else _build_deploy_request_or_fail(
                ip=ip,
                domain=domain,
                sni=sni,
                client_name=client_name,
                user=user,
                yes=yes,
                harden=harden,
                requested_server=requested_server,
                server_name=server_name,
                icon=icon,
                color=color,
                decoy=decoy,
                pq=pq,
                warp=warp,
                geo_block=geo_block,
                ssh_port=ssh_port,
            )
        )
        machine_mode = final_json or bool(request_path) or dry_run
        if build_deploy_workflow(request).needs_input:
            if machine_mode:
                fail(
                    "Deploy request is incomplete",
                    hint="Call `meridian api workflow deploy --json`, collect fields, then pass --request FILE.",
                    hint_type="user",
                )
            wizard_result = _interactive_wizard(
                sni=sni,
                domain=domain,
                harden=harden,
                yes=yes,
                client_name=client_name,
                server_name=server_name,
                icon=icon,
                color=color,
                pq=pq,
                warp=warp,
                geo_block=geo_block,
            )
            ip, user, sni, domain, harden = wizard_result[:5]
            client_name, server_name, icon, color, pq, warp, geo_block = wizard_result[5:]
            request = apply_deploy_workflow_answers(
                request,
                DeployWorkflowAnswers(
                    ip=ip,
                    domain=domain,
                    sni=sni,
                    client_name=client_name,
                    user=user,
                    harden=harden,
                    server_name=server_name,
                    icon=icon,
                    color=color,
                    pq=pq,
                    warp=warp,
                    geo_block=geo_block,
                    confirm=True,
                ),
            )

        if dry_run:
            registry = ServerRegistry(SERVERS_FILE)
            try:
                plan = dry_run_deploy_request(request, cluster=ClusterConfig.load(), registry=registry)
            except EngineError as exc:
                fail(str(exc), hint=exc.hint, hint_type=exc.category)
            _emit_deploy_started_event(reporter, operation, dry_run=True)
            _emit_deploy_completed_event(reporter, operation, plan, dry_run=True)
            if final_json:
                _emit_deploy_json(plan, operation=operation, status="ok")
            else:
                err_console.print()
                err_console.print(f"  [bold]Deploy dry-run:[/bold] {plan.mode} for {plan.server_ip}")
                err_console.print()
            return

        if machine_mode and not request.yes:
            fail(
                "Machine deploy requires confirmation in the request",
                hint="Set `yes: true` after the UI user confirms the deployment.",
                hint_type="user",
            )

        result = deploy_server(request, executor=_execute_deploy_request, reporter=reporter, operation=operation)
        if final_json:
            _emit_deploy_json(result, operation=operation, status="changed")


@contextmanager
def _quiet_machine_output(enabled: bool) -> Iterator[None]:
    """Temporarily suppress human stderr rendering for JSON deploys."""
    if not enabled:
        yield
        return

    previous = is_quiet_mode()
    set_quiet_mode(True)
    try:
        yield
    finally:
        set_quiet_mode(previous)


def _load_deploy_request(source: str) -> DeployRequest:
    """Load a DeployRequest from a JSON file or stdin."""
    try:
        text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"Could not read deploy request: {exc}", hint_type="user")
    try:
        return DeployRequest.model_validate_json(text)
    except ValidationError as exc:
        error = wrap_validation_error("Invalid deploy request JSON", exc)
        fail(str(error), hint=error.hint, hint_type="user")


def _build_deploy_request_or_fail(**fields: Any) -> DeployRequest:
    """Build a DeployRequest and convert model errors into CLI-facing hints."""
    try:
        return DeployRequest(**fields)
    except ValidationError as exc:
        error = wrap_validation_error("Invalid deploy request", exc)
        fail(str(error), hint=error.hint, hint_type="user")


def _normalize_deploy_request_or_fail(request: DeployRequest) -> DeployRequest:
    try:
        return normalize_deploy_request(request)
    except DeployValidationError as exc:
        fail(str(exc), hint=exc.hint, hint_type="user")


def _emit_deploy_json(result: DeployResult | DeployPlan, *, operation: OperationContext, status: OutputStatus) -> None:
    """Emit a typed deploy envelope."""
    if isinstance(result, DeployResult):
        summary_text = result.summary
        counts = {"nodes": result.node_count, "relays": result.relay_count}
    else:
        summary_text = f"Deploy plan: {result.mode} for {result.server_ip}"
        counts = {"nodes": result.node_count, "relays": result.relay_count}

    emit_json(
        command_envelope(
            command="deploy",
            data=result.model_dump(mode="json"),
            summary=Summary(text=summary_text, changed=status == "changed", counts=counts),
            status=status,
            exit_code=0,
            timer=operation.timer,
        )
    )


def _emit_deploy_started_event(reporter: Reporter | None, operation: OperationContext, *, dry_run: bool) -> None:
    if reporter is None:
        return
    emit_event(
        reporter,
        operation,
        COMMAND_STARTED,
        phase="deploy",
        message="Deploy dry-run started" if dry_run else "Deploy started",
        data={"command": "deploy", "dry_run": dry_run},
    )


def _emit_deploy_completed_event(
    reporter: Reporter | None,
    operation: OperationContext,
    plan: DeployPlan,
    *,
    dry_run: bool,
) -> None:
    if reporter is None:
        return
    emit_event(
        reporter,
        operation,
        COMMAND_COMPLETED,
        phase="deploy",
        message="Deploy dry-run completed" if dry_run else "Deploy completed",
        data={"command": "deploy", "dry_run": dry_run, "mode": plan.mode, "server_ip": plan.server_ip},
    )


def _execute_deploy_request(
    request: DeployRequest,
    reporter: Reporter | None = None,
    operation: OperationContext | None = None,
) -> DeployResult:
    """Execute deploy request using the current SSH/panel implementation."""
    request = _normalize_deploy_request_or_fail(request)

    domain = request.domain
    sni = request.sni
    client_name = request.client_name
    yes = request.yes
    harden = request.harden
    server_name = request.server_name
    icon = request.icon
    color = request.color
    pq = request.pq
    warp = request.warp
    geo_block = request.geo_block

    # request.decoy is deprecated (403/404 is now always the default).
    # Accept silently for backwards compatibility but don't use it.

    registry = ServerRegistry(SERVERS_FILE)
    try:
        target = resolve_deploy_target(request, registry)
    except EngineError as exc:
        fail(str(exc), hint=exc.hint, hint_type=exc.category)

    # Resolve and prepare SSH connection
    resolved = resolve_server(
        registry,
        explicit_ip=target.server_ip,
        user=target.ssh_user,
        port=target.ssh_port,
    )
    resolved = ensure_server_connection(resolved)
    _check_ports(resolved.conn, resolved.ip, yes)

    # Load existing cluster config
    cluster = ClusterConfig.load()

    # Only check for legacy 3x-ui on first deploy — redeploy means v4 is already running
    if not cluster.is_configured:
        _check_legacy_panel(resolved.conn, resolved.ip, yes)

    # Load existing cluster config
    cluster = ClusterConfig.load()

    try:
        deploy_plan = plan_deploy_request(
            request.model_copy(update={"ip": resolved.ip, "requested_server": ""}),
            cluster=cluster,
            registry=registry,
        )
    except EngineError as exc:
        fail(str(exc), hint=exc.hint, hint_type=exc.category)

    is_first_deploy = deploy_plan.mode == "first_deploy"
    is_redeploy = deploy_plan.mode == "redeploy"
    if is_first_deploy:
        info("First deployment -- will set up panel + proxy node")
    else:
        info(f"Redeploying existing node at {resolved.ip}")

    xhttp_port = deploy_plan.ports.xhttp_port
    reality_port = deploy_plan.ports.reality_port
    wss_port = deploy_plan.ports.wss_port
    secret_path = deploy_plan.secret_path
    xhttp_path = deploy_plan.xhttp_path
    ws_path = deploy_plan.ws_path
    info_page_path = deploy_plan.info_page_path

    # Build and run provisioner pipeline
    _run_provisioner(
        resolved=resolved,
        cluster=cluster,
        domain=domain,
        sni=sni,
        harden=harden,
        is_panel_host=is_first_deploy,
        secret_path=secret_path,
        xhttp_port=xhttp_port,
        reality_port=reality_port,
        wss_port=wss_port,
        pq=pq,
        warp=warp,
        geo_block=geo_block,
        xhttp_path=xhttp_path,
        ws_path=ws_path,
        info_page_path=info_page_path,
        reporter=reporter,
        operation=operation,
    )

    # Post-provisioner: configure panel via REST API
    _configure_panel_and_node(
        resolved=resolved,
        cluster=cluster,
        domain=domain,
        sni=sni or DEFAULT_SNI,
        client_name=client_name,
        is_first_deploy=is_first_deploy,
        is_redeploy=is_redeploy,
        secret_path=secret_path,
        reality_port=reality_port,
        xhttp_port=xhttp_port,
        wss_port=wss_port,
        pq=pq,
        warp=warp,
        geo_block=geo_block,
        xhttp_path=xhttp_path,
        ws_path=ws_path,
        info_page_path=info_page_path,
    )

    # Save branding
    if server_name or icon or color:
        cluster.branding = BrandingConfig(
            server_name=server_name,
            icon=icon,
            color=color,
        )

    # Save cluster config
    cluster.save()
    ok("Cluster configuration saved")

    # Persist sub_path on server for fleet recovery (not stored in panel API)
    if cluster.panel.sub_path:
        try:
            resolved.conn.put_text(
                "/etc/meridian/sub_path",
                cluster.panel.sub_path,
                mode="600",
                sensitive=True,
                timeout=10,
            )
        except Exception:
            pass  # Non-fatal

    # Register server in legacy registry (for --server flag resolution)
    registry.add(ServerEntry(host=resolved.ip, user=resolved.user, port=getattr(resolved.conn, "port", 22)))

    # Success output
    redeploy_cmd = _build_redeploy_command(
        resolved,
        sni=sni,
        domain=domain,
        client_name=client_name,
        harden=harden,
        server_name=server_name,
        icon=icon,
        color=color,
        pq=pq,
        warp=warp,
        geo_block=geo_block,
    )
    _print_success(
        resolved=resolved,
        cluster=cluster,
        client_name=client_name,
        domain=domain,
        redeploy_cmd=redeploy_cmd,
    )

    # Offer relay setup
    _offer_relay(resolved, yes)

    return DeployResult(
        mode="first_deploy" if is_first_deploy else "redeploy",
        server_ip=resolved.ip,
        ssh_user=resolved.user,
        ssh_port=getattr(resolved.conn, "port", target.ssh_port),
        domain=domain,
        sni=sni or DEFAULT_SNI,
        client_name=client_name,
        harden=harden,
        pq=pq,
        warp=warp,
        geo_block=geo_block,
        panel_url=cluster.panel.url,
        panel_secret_path=cluster.panel.secret_path,
        connection_page_path=cluster.panel.sub_path,
        connection_page_url=str(cluster._extra.get("_page_url", "")),
        subscription_url=str(cluster._extra.get("_subscription_url", "")),
        test_command=f"meridian test {resolved.ip}",
        node_count=len(cluster.nodes),
        relay_count=len(cluster.relays),
        summary=f"Deploy completed for {resolved.ip}",
    )


# ---------------------------------------------------------------------------
# Provisioner pipeline + panel bootstrap — extracted to panel_bootstrap.py
# ---------------------------------------------------------------------------
# Backward-compatible aliases so tests that patch "meridian.commands.setup._X"
# continue to work. New code should import from meridian.panel_bootstrap.

from meridian.panel_bootstrap import (  # noqa: F401 — backward-compat aliases for test patches
    build_xray_config as _build_xray_config,
    cache_inbounds as _cache_inbounds,
    configure_panel_and_node as _configure_panel_and_node,
    create_api_token as _create_api_token,
    create_hosts_for_node as _create_hosts_for_node,
    deploy_client_page as _deploy_client_page,
    deploy_node_container as _deploy_node_container,
    generate_reality_keypair as _generate_reality_keypair,
    get_docker_gateway as _get_docker_gateway,
    panel_base_url as _panel_base_url,
    run_provisioner as _run_provisioner,
    select_default_squad_uuid as _select_default_squad_uuid,
    setup_first_deploy as _setup_first_deploy,
    setup_new_node as _setup_new_node,
    setup_redeploy as _setup_redeploy,
    wait_for_panel_api as _wait_for_panel_api,
)


# ---------------------------------------------------------------------------
# Port check
# ---------------------------------------------------------------------------


def _check_ports(conn: ServerConnection, ip: str, yes: bool) -> None:
    """Check that ports 443 and 80 are available before deploying.

    Allows re-deploy over existing Meridian processes.
    Loops with retry prompt if a non-Meridian process holds a port.
    """
    allowed = {"nginx", "xray", "haproxy", "caddy", "remnawave", "remnawave-node", "docker-proxy"}

    for port in (443, 80):
        while True:
            result = conn.run(f"ss -tlnp sport = :{port} 2>/dev/null | grep LISTEN", timeout=10)
            if not result.stdout.strip():
                break  # port free

            match = re.search(r'users:\(\("([^"]*)"', result.stdout)
            proc = match.group(1) if match else "unknown"
            if proc in allowed:
                break  # Meridian's own process -- OK for re-deploy

            warn(f"Port {port} is in use by {proc}")
            if not is_quiet_mode():
                err_console.print(f"  [dim]Port {port} must be free for Meridian.[/dim]")
                err_console.print(f"  [dim]Stop {proc} and retry, or press Ctrl+C to abort.[/dim]")
                err_console.print()

            if yes:
                fail(
                    f"Port {port} is occupied by {proc}",
                    hint=f"Stop {proc} first: sudo systemctl stop {proc}",
                    hint_type="user",
                )

            choice = choose("Retry?", ["Yes", "No"])
            if choice == 2:
                fail("Aborted -- port conflict", hint_type="user")


def _check_legacy_panel(conn: ServerConnection, server_ip: str, yes: bool) -> None:
    """Detect a running 3x-ui panel from Meridian 3.x and warn the user.

    Shows migration context inline (client names, what will break) so the
    user can make an informed decision without leaving the deploy flow.
    The actual cleanup happens in the provisioner pipeline
    (CleanupLegacyPanel step).
    """
    result = conn.run("docker inspect -f '{{.State.Status}}' 3x-ui 2>/dev/null", timeout=15)
    if result.returncode != 0 or not result.stdout.strip():
        return  # no 3x-ui container

    from rich.panel import Panel

    from meridian.config import CREDS_BASE
    from meridian.credentials import ServerCredentials

    # Try to read old v3 credentials for this server
    client_names: list[str] = []
    proxy_path = CREDS_BASE / server_ip / "proxy.yml"
    if proxy_path.exists():
        try:
            creds = ServerCredentials.load(proxy_path)
            client_names = [c.name for c in creds.clients if c.name]
        except Exception:
            pass

    lines = [
        "This server has a Meridian 3.x deployment (3x-ui).",
        "Meridian 4.0 replaces 3x-ui with [bold]Remnawave[/bold] -- a new panel.",
        "",
        "  [yellow]\u2022[/yellow] 3x-ui will be stopped and removed",
        "  [yellow]\u2022[/yellow] Existing client connection configs will stop working",
    ]
    if client_names:
        names = ", ".join(f"[bold]{n}[/bold]" for n in client_names)
        lines.append(f"  [yellow]\u2022[/yellow] Re-create clients after deploy: {names}")
    lines.append("  [yellow]\u2022[/yellow] Clients will need new QR codes / subscription links")

    warning = "\n".join(lines)
    if not is_quiet_mode():
        err_console.print()
        err_console.print(
            Panel(
                warning,
                title="[bold yellow]Upgrading from 3.x[/bold yellow]",
                border_style="yellow",
                padding=(0, 2),
            )
        )

    if not yes:
        if not confirm("Continue with deployment?"):
            raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Interactive wizard
# ---------------------------------------------------------------------------


def _interactive_wizard(
    sni: str,
    domain: str,
    harden: bool,
    yes: bool,
    client_name: str = "",
    server_name: str = "",
    icon: str = "",
    color: str = "",
    pq: bool = False,
    warp: bool = False,
    geo_block: bool = True,
) -> tuple[str, str, str, str, bool, str, str, str, str, bool, bool, bool]:
    """Interactive deployment wizard.

    Returns (ip, user, sni, domain, harden,
             client_name, server_name, icon, color, pq, warp, geo_block).
    """
    import os

    # --- Protocol explanation ---
    err_console.print()
    info("Protocol: VLESS + Reality")
    err_console.print("  [dim]Your server impersonates a real website -- censors see[/dim]")
    err_console.print("  [dim]normal HTTPS traffic, not a VPN connection.[/dim]")
    err_console.print()

    # --- Server IP ---
    detected_ip = detect_public_ip()
    is_local = False

    # Offer local deployment if running as root with a public IP
    if detected_ip and os.getuid() == 0:
        info(f"Detected: running as root on this server ({detected_ip})")
        err_console.print()
        choice = choose(
            "Deploy target",
            [
                f"This server ({detected_ip}) -- local mode",
                "Another server -- enter IP",
            ],
        )
        if choice == 1:
            server_ip = "local"
            ssh_user = "root"
            is_local = True
        else:
            is_local = False

    if not is_local:
        while True:
            server_ip = prompt("Server IP address", default=detected_ip)
            if is_ip(server_ip) or is_local_keyword(server_ip):
                break
            err_console.print("  [error]Enter a valid IP address (e.g. 123.45.67.89)[/error]")

        if is_local_keyword(server_ip):
            is_local = True
            ssh_user = "root"
        else:
            # --- SSH user ---
            while True:
                ssh_user = prompt("SSH user", default="root")
                if re.match(r"^[a-zA-Z0-9._-]+$", ssh_user):
                    break
                err_console.print("  [error]Use letters, numbers, dots, hyphens, and underscores only[/error]")
            if ssh_user != "root":
                err_console.print("  [dim](sudo will be used for privileged operations)[/dim]")

    # --- Server hardening ---
    if not yes:
        err_console.print()
        err_console.print("  [bold]Server hardening[/bold]")
        err_console.print("  [dim]Disables password SSH login and enables firewall[/dim]")
        err_console.print("  [dim](allows ports 22, 80, 443 only). Skip if you have[/dim]")
        err_console.print("  [dim]other services running on this server.[/dim]")
        err_console.print()
        choice = choose(
            "Choose",
            [
                "Yes -- harden SSH and firewall [dim](recommended)[/dim]",
                "No -- keep current settings",
            ],
        )
        if choice == 2:
            harden = False
            warn("Skipping SSH hardening and firewall")
        else:
            harden = True

    # --- Camouflage target (SNI) ---
    if not sni:
        err_console.print()
        err_console.print("  [bold]Camouflage target[/bold]")
        err_console.print("  [dim]Pick any popular website (you don't need to own it).[/dim]")
        err_console.print("  [dim]Your server will impersonate it -- censors probing see[/dim]")
        err_console.print("  [dim]that real site's certificate. Scanning finds targets on[/dim]")
        err_console.print("  [dim]the same network, which are hardest to distinguish.[/dim]")
        err_console.print()

        if not yes:
            choice = choose(
                "Camouflage",
                [
                    "Scan for optimal target (~1 minute)",
                    "Enter manually",
                    f"Skip -- use default ({DEFAULT_SNI})",
                ],
            )
            if choice == 2:
                sni = prompt("SNI domain (e.g. example.com)")
            elif choice == 1:
                # Establish connection for scan
                try:
                    scan_ip = detected_ip if is_local else server_ip
                    conn = ServerConnection(ip=scan_ip, user=ssh_user, local_mode=is_local)
                    if not is_local:
                        conn.detect_local_mode()
                        if not conn.local_mode:
                            conn.check_ssh()

                    from meridian.commands.scan import scan_for_sni

                    candidates = scan_for_sni(conn, scan_ip)

                    if candidates:
                        top = candidates[:5]
                        options = list(top) + [f"[dim]Skip -- use default ({DEFAULT_SNI})[/dim]"]
                        err_console.print()
                        pick = choose("Choose", options)
                        if pick <= len(top):
                            sni = top[pick - 1]
                    else:
                        warn("No targets found on the same network")
                except Exception:
                    warn("Could not connect to scan. You can run 'meridian scan' later.")

        if not sni:
            sni = DEFAULT_SNI

    # --- Domain (optional but strongly recommended) ---
    err_console.print()
    err_console.print("  [bold]Domain[/bold] [dim](strongly recommended)[/dim]")
    err_console.print("  [dim]Makes your server indistinguishable from a normal website.[/dim]")
    err_console.print("  [dim]Without a domain, probers see an IP-only certificate --[/dim]")
    err_console.print("  [dim]a valid but less common profile. Also enables CDN fallback.[/dim]")
    err_console.print("  [dim]Guide: getmeridian.org/docs/en/domain-mode/[/dim]")

    if not yes:
        domain_input = prompt("Domain (leave blank to skip)", default=domain or "")
        if domain_input and domain_input != "skip":
            domain = domain_input
        elif not domain_input or domain_input == "skip":
            domain = ""
    elif not domain:
        domain = ""

    # --- Branding: server name ---
    if not yes and not server_name:
        err_console.print()
        err_console.print("  [bold]Personalize[/bold]")
        err_console.print("  [dim]Make the connection page yours. Your friends see this.[/dim]")
        err_console.print()
        server_name = prompt("Server name", default="My VPN")

    # --- Branding: icon ---
    if not yes and not icon:
        from meridian.branding import ICON_SUGGESTIONS

        err_console.print()
        err_console.print("  [bold]Server icon[/bold]")
        grid = "    "
        for i, emoji in enumerate(ICON_SUGGESTIONS, 1):
            grid += f"{i}. {emoji}  "
        err_console.print(grid)
        err_console.print()
        icon_input = prompt("Pick a number, paste an emoji, or an image URL", default="1")
        if icon_input.isdigit():
            idx = int(icon_input) - 1
            if 0 <= idx < len(ICON_SUGGESTIONS):
                icon = ICON_SUGGESTIONS[idx]
        if not icon:
            from meridian.branding import process_icon

            icon = process_icon(icon_input)
    elif icon:
        # CLI flag provided -- process it
        from meridian.branding import process_icon

        processed = process_icon(icon)
        if processed:
            icon = processed

    # --- Branding: color palette ---
    if not yes and not color:
        from meridian.branding import PALETTE_LABELS, PALETTES

        err_console.print()
        err_console.print("  [bold]Color palette[/bold]")
        palette_names = list(PALETTES.keys())
        options = []
        for pname in palette_names:
            marker = " [dim](default)[/dim]" if pname == "ocean" else ""
            options.append(f"{PALETTE_LABELS[pname]}{marker}")
        err_console.print()
        color_pick = choose("Choose", options)
        idx = color_pick - 1
        if 0 <= idx < len(palette_names):
            color = palette_names[idx]
    elif color:
        from meridian.branding import validate_color

        color = validate_color(color) or "ocean"

    if not color:
        color = "ocean"

    # --- Client name ---
    if not yes and not client_name:
        err_console.print()
        err_console.print("  [bold]First client[/bold]")
        err_console.print("  [dim]Name for the first connection profile (you can add more later).[/dim]")
        err_console.print()
        client_name = prompt("Client name", default="default")

    if not client_name:
        client_name = "default"

    # --- Post-quantum encryption ---
    if not yes and not pq:
        err_console.print()
        err_console.print("  [bold]Post-quantum encryption[/bold] [dim](experimental)[/dim]")
        err_console.print("  [dim]Adds ML-KEM-768 hybrid encryption on top of Reality.[/dim]")
        err_console.print("  [dim]Only tested with Happ and v2RayTun. Some apps may not connect.[/dim]")
        err_console.print()
        choice = choose(
            "Choose",
            [
                "No -- standard encryption [dim](all apps)[/dim]",
                "Yes -- post-quantum [dim](tested: Happ, v2RayTun)[/dim]",
            ],
        )
        if choice == 2:
            pq = True

    # --- Cloudflare WARP ---
    if not yes and not warp:
        err_console.print()
        err_console.print("  [bold]Cloudflare WARP[/bold] [dim](optional)[/dim]")
        err_console.print("  [dim]Routes outgoing traffic through Cloudflare so websites[/dim]")
        err_console.print("  [dim]see a Cloudflare IP, not your server's real IP.[/dim]")
        err_console.print()
        err_console.print("  [dim]Useful when:[/dim]")
        err_console.print("  [dim]  * Websites block datacenter/VPS IP ranges[/dim]")
        err_console.print("  [dim]  * You want to hide the VPS IP from destination sites[/dim]")
        err_console.print()
        err_console.print("  [dim]Not needed when:[/dim]")
        err_console.print("  [dim]  * Normal browsing already works fine through the proxy[/dim]")
        err_console.print("  [dim]  * You want maximum speed (WARP adds an extra hop)[/dim]")
        err_console.print()
        choice = choose(
            "Choose",
            [
                "No -- direct connection [dim](default, fastest)[/dim]",
                "Yes -- route through Cloudflare WARP",
            ],
        )
        if choice == 2:
            warp = True

    # --- Geo-blocking ---
    if not yes and geo_block:
        err_console.print()
        err_console.print("  [bold]Geo-blocking[/bold]")
        err_console.print("  [dim]Blocks access to Russian websites and IPs through[/dim]")
        err_console.print("  [dim]the proxy (geosite:category-ru + geoip:ru).[/dim]")
        err_console.print()
        err_console.print("  [dim]Why enable:[/dim]")
        err_console.print("  [dim]  * Prevents your VPN server IP from appearing in logs[/dim]")
        err_console.print("  [dim]    of Russian services -- reduces risk of it being blocked[/dim]")
        err_console.print("  [dim]  * Russian sites work fine without a VPN anyway[/dim]")
        err_console.print()
        err_console.print("  [dim]Why disable:[/dim]")
        err_console.print("  [dim]  * You need to access .ru sites through the proxy[/dim]")
        err_console.print("  [dim]  * You want all traffic to go through the VPN with no[/dim]")
        err_console.print("  [dim]    exceptions[/dim]")
        err_console.print()
        choice = choose(
            "Choose",
            [
                "Yes -- block Russian traffic [dim](recommended, protects server IP)[/dim]",
                "No -- allow all traffic [dim](Russian sites accessible through proxy)[/dim]",
            ],
        )
        if choice == 2:
            geo_block = False

    # --- Summary panel ---
    from rich.panel import Panel

    protocol_line = "VLESS + Reality (TCP)\n           + XHTTP fallback (same port)"
    if domain:
        protocol_line += f"\n           + CDN fallback ({domain})"

    encryption_line = ""
    if pq:
        encryption_line = "\nEncryption: Post-quantum (ML-KEM-768 hybrid) [dim]experimental[/dim]"

    warp_line = ""
    if warp:
        warp_line = "\nWARP:       Outgoing traffic via Cloudflare"

    geo_block_line = (
        "\nGeo-block:  Enabled (.ru / Russian IP traffic blocked)"
        if geo_block
        else "\nGeo-block:  Disabled (Russian sites accessible)"
    )

    icon_display = icon if icon and not icon.startswith("data:") else ""
    branding_line = ""
    if server_name or icon_display or color:
        parts = []
        if icon_display:
            parts.append(icon_display)
        if server_name:
            parts.append(server_name)
        if color:
            parts.append(f"[dim]{color} palette[/dim]")
        branding_line = f"\nBranding:   {' '.join(parts)}"

    server_label = f"this server ({detected_ip}) -- local mode" if is_local else f"{ssh_user}@{server_ip}"
    harden_label = "SSH hardened + firewall" if harden else "skipped"
    summary = (
        f"Server:     {server_label}\n"
        f"Protocol:   {protocol_line}\n"
        f"Camouflage: {sni}\n"
        f"Hardening:  {harden_label}\n"
        f"Client:     {client_name}\n"
        f"Mode:       {'Domain mode (best stealth + CDN fallback)' if domain else 'IP-only (works without a domain)'}"
        f"{encryption_line}"
        f"{warp_line}"
        f"{geo_block_line}"
        f"{branding_line}"
    )

    err_console.print()
    err_console.print(Panel(summary, title="[bold]Deployment plan[/bold]", border_style="cyan", padding=(0, 2)))
    err_console.print()

    # --- Confirm ---
    if not yes:
        if is_local:
            if not confirm(f"Deploy locally on this server ({detected_ip})?"):
                raise typer.Exit(1)
        else:
            if not confirm(f"Deploy to {ssh_user}@{server_ip}?"):
                raise typer.Exit(1)
    err_console.print()

    return server_ip, ssh_user, sni, domain, harden, client_name, server_name, icon, color, pq, warp, geo_block


# ---------------------------------------------------------------------------
# Success output
# ---------------------------------------------------------------------------


def _build_redeploy_command(
    resolved: ResolvedServer,
    *,
    sni: str,
    domain: str,
    client_name: str,
    harden: bool,
    server_name: str,
    icon: str,
    color: str,
    pq: bool,
    warp: bool,
    geo_block: bool,
) -> str:
    """Build a non-interactive meridian deploy command that reproduces the current config."""
    parts = ["meridian deploy", shlex.quote(resolved.ip)]

    if resolved.user != "root":
        parts.append(f"--user {shlex.quote(resolved.user)}")
    if sni and sni != DEFAULT_SNI:
        parts.append(f"--sni {shlex.quote(sni)}")
    if domain:
        parts.append(f"--domain {shlex.quote(domain)}")
    if client_name and client_name != "default":
        parts.append(f"--client-name {shlex.quote(client_name)}")
    if not harden:
        parts.append("--no-harden")
    if pq:
        parts.append("--pq")
    if warp:
        parts.append("--warp")
    if not geo_block:
        parts.append("--no-geo-block")
    if server_name:
        parts.append(f"--display-name {shlex.quote(server_name)}")
    if icon:
        parts.append(f"--icon {shlex.quote(icon)}")
    if color and color != "ocean":
        parts.append(f"--color {shlex.quote(color)}")
    parts.append("--yes")

    return " ".join(parts)


def _print_success(
    resolved: ResolvedServer,
    cluster: ClusterConfig,
    client_name: str,
    domain: str,
    *,
    redeploy_cmd: str,
) -> None:
    """Print success output after deployment."""
    if is_quiet_mode():
        return

    client_label = client_name or "default"
    server_ip = resolved.ip

    # Build subscription URL if panel is configured
    sub_url = ""
    page_url = ""
    if cluster.panel.url and cluster.panel.api_token:
        try:
            with MeridianPanel(cluster.panel.url, cluster.panel.api_token) as panel:
                user = panel.get_user(client_label)
                if user and user.short_uuid:
                    sub_url = panel.get_subscription_url(user.short_uuid)
                # Build connection page URL from cluster data
                if user and user.vless_uuid:
                    node = cluster.panel_node
                    info_path = cluster.panel.sub_path or ""
                    if node and info_path:
                        host = node.domain or node.ip
                        page_url = f"https://{host}/{info_path}/{user.vless_uuid}/"
        except RemnawaveError:
            pass

    err_console.print("\n  [ok][bold]Done![/bold][/ok]\n")
    ok("Your proxy server is live and ready to use.")
    err_console.print()
    err_console.print("  [bold]Next steps:[/bold]\n")

    step = 1

    if page_url:
        err_console.print(f"  [ok]{step}.[/ok] Share this link with whoever needs access:")
        err_console.print(f"     [bold]{page_url}[/bold]")
        err_console.print("     [dim](They open it, scan the QR code, and connect)[/dim]\n")
        step += 1

    if sub_url:
        err_console.print(f"  [ok]{step}.[/ok] Or import as a subscription (for Xray/V2Ray apps):")
        err_console.print(f"     [bold]{sub_url}[/bold]\n")
        step += 1
    elif not page_url:
        err_console.print(f"  [ok]{step}.[/ok] View connection details:")
        err_console.print(f"     [info]meridian client show {client_label}[/info]\n")
        step += 1

    err_console.print(f"  [ok]{step}.[/ok] Test that the proxy works:")
    err_console.print(f"     [info]meridian test {server_ip}[/info]")
    err_console.print("     [dim]Run it after deploy/redeploy to verify the live server state.[/dim]\n")
    step += 1

    if cluster.panel.url:
        err_console.print(f"  [ok]{step}.[/ok] Manage clients:")
        err_console.print("     [info]meridian client add alice[/info]")
        err_console.print("     [info]meridian client list[/info]\n")
        step += 1

    if domain:
        err_console.print(f"  [ok]{step}.[/ok] Cloudflare setup:")
        err_console.print(f"     [dim]A record {domain} -> {server_ip}[/dim]")
        err_console.print("     [dim]Keep it DNS only (grey cloud) during deploy/redeploy[/dim]")
        err_console.print("     [dim]After deploy succeeds: switch to Proxied (orange cloud)[/dim]")
        err_console.print("     [dim]Set SSL/TLS to Full (Strict) and enable WebSockets[/dim]\n")
        err_console.print("     [dim]Disable features that inject scripts or rewrite HTML on this hostname[/dim]")
        err_console.print("     [dim](for example Website Analytics / RUM), or the connection page can break[/dim]\n")
        step += 1

    err_console.print(f"  [ok]{step}.[/ok] Add a relay for resilience (optional):")
    err_console.print(f"     [info]meridian relay deploy RELAY_IP --exit {server_ip}[/info]")
    err_console.print("     [dim]Routes through a domestic IP when the exit gets blocked[/dim]\n")

    err_console.print()
    line()

    # Redeploy command
    err_console.print("\n  [dim]Re-deploy with the same settings:[/dim]")
    err_console.print(f"  [dim]  {redeploy_cmd}[/dim]")

    # Panel access for advanced users
    if cluster.panel.url:
        err_console.print("\n  [dim]Remnawave panel (advanced -- manage nodes, monitor traffic):[/dim]")
        err_console.print(f"  [dim]  {cluster.panel.display_url}[/dim]")

    err_console.print("\n  [dim]Feedback & issues: https://github.com/uburuntu/meridian/issues[/dim]\n")


def _offer_relay(resolved: ResolvedServer, yes: bool) -> None:
    """Offer to deploy a relay node after successful exit server deploy."""
    if yes:
        return  # Don't prompt in non-interactive mode

    err_console.print()
    err_console.print("  [bold]Add a relay node?[/bold] [dim](optional)[/dim]")
    err_console.print("  [dim]A relay is a domestic server that forwards traffic to[/dim]")
    err_console.print("  [dim]this exit server. Useful when the IP gets blocked.[/dim]")
    err_console.print()

    choice = choose(
        "Set up a relay?",
        [
            "No -- skip for now",
            "Yes -- add a relay node",
        ],
    )
    if choice == 1:
        err_console.print(f"  [dim]You can add one later: meridian relay deploy RELAY_IP --exit {resolved.ip}[/dim]")
        return

    relay_ip = prompt("Relay server IP")
    if not is_ip(relay_ip):
        warn(f"Invalid IP. Set up later: meridian relay deploy RELAY_IP --exit {resolved.ip}")
        return

    relay_name = prompt("Relay name (optional, e.g. ru-moscow)", default="")

    from meridian.commands.relay import run_deploy

    run_deploy(
        relay_ip=relay_ip,
        exit_arg=resolved.ip,
        user="root",
        relay_name=relay_name,
        listen_port=443,
        yes=False,
    )
