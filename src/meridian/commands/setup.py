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

from pydantic import ValidationError

from meridian.adapters import JsonlReporter
from meridian.cluster import (
    BrandingConfig,
    ClusterConfig,
)
from meridian.commands.resolve import (
    ensure_server_connection,
    resolve_server,
)
from meridian.config import (
    DEFAULT_SNI,
    SERVER_PROFILES_FILE,
    is_ip,
)
from meridian.console import (
    choose,
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
from meridian.core.errors import EngineError, MeridianError
from meridian.core.events import COMMAND_COMPLETED, COMMAND_STARTED
from meridian.core.models import OutputStatus, Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.reporters import NoopReporter, Reporter, emit_event
from meridian.core.services.deploy import deploy_server
from meridian.core.validation import wrap_validation_error
from meridian.engine.deploy import dry_run_deploy_request, plan_deploy_request, resolve_deploy_target
from meridian.panel_bootstrap import configure_panel_and_node, run_provisioner
from meridian.provision.progress import RichStepRenderer
from meridian.remnawave import MeridianPanel, RemnawaveError
from meridian.renderers import emit_json
from meridian.resolve import ResolvedServer
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
    reporter: Reporter = JsonlReporter() if events == "jsonl" else NoopReporter()

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
            from meridian.commands.wizard import interactive_wizard

            wizard_result = interactive_wizard(
                sni=sni,
                domain=domain,
                harden=harden,
                yes=yes,
                client_name=client_name,
                server_name=server_name,
                icon=icon,
                color=color,
                warp=warp,
                geo_block=geo_block,
            )
            ip = wizard_result.ip
            user = wizard_result.user
            sni = wizard_result.sni
            domain = wizard_result.domain
            harden = wizard_result.harden
            client_name = wizard_result.client_name
            server_name = wizard_result.server_name
            icon = wizard_result.icon
            color = wizard_result.color
            warp = wizard_result.warp
            geo_block = wizard_result.geo_block
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
                    warp=warp,
                    geo_block=geo_block,
                    confirm=True,
                ),
            )

        if dry_run:
            registry = ServerRegistry(SERVER_PROFILES_FILE)
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


def _emit_deploy_started_event(reporter: Reporter, operation: OperationContext, *, dry_run: bool) -> None:
    emit_event(
        reporter,
        operation,
        COMMAND_STARTED,
        phase="deploy",
        message="Deploy dry-run started" if dry_run else "Deploy started",
        data={"command": "deploy", "dry_run": dry_run},
    )


def _emit_deploy_completed_event(
    reporter: Reporter,
    operation: OperationContext,
    plan: DeployPlan,
    *,
    dry_run: bool,
) -> None:
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
    reporter: Reporter = NoopReporter(),
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
    warp = request.warp
    geo_block = request.geo_block

    registry = ServerRegistry(SERVER_PROFILES_FILE)
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

    # Build and run provisioner pipeline + configure panel via REST API
    try:
        run_provisioner(
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
            warp=warp,
            geo_block=geo_block,
            xhttp_path=xhttp_path,
            ws_path=ws_path,
            info_page_path=info_page_path,
            reporter=reporter,
            operation=operation,
            renderer=RichStepRenderer() if not is_quiet_mode() else None,
        )

        # Post-provisioner: configure panel via REST API
        configure_panel_and_node(
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
            warp=warp,
            geo_block=geo_block,
            xhttp_path=xhttp_path,
            ws_path=ws_path,
            info_page_path=info_page_path,
        )
    except MeridianError as exc:
        fail(exc)

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
        except (OSError, RuntimeError):
            pass  # Non-fatal

    # Register server for --server flag resolution
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

    err_console.print("\n  [dim]Feedback & issues: https://github.com/getmeridian/meridian/issues[/dim]\n")


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
