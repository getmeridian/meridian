"""Deploy proxy server — interactive wizard and provisioner execution.

Meridian 4.0: Remnawave panel + node architecture. The provisioner deploys
containers via SSH, then this module calls the Remnawave REST API directly
from the deployer's machine to configure users, profiles, and hosts.
"""

from __future__ import annotations

import re
import secrets
import shlex
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError

from meridian.adapters import JsonlReporter, RemoteExecutorConnection, SSHRemoteExecutor
from meridian.cluster import (
    BrandingConfig,
    ClusterConfig,
    InboundRef,
    NodeEntry,
    PanelConfig,
    ProtocolKey,
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
    REMNAWAVE_NODE_API_PORT,
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
from meridian.core.execution import RemoteExecutor
from meridian.core.models import OutputStatus, Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.reporters import Reporter, emit_event
from meridian.core.services.deploy import deploy_server
from meridian.core.validation import wrap_validation_error
from meridian.engine.deploy import EngineError, dry_run_deploy_request, plan_deploy_request, resolve_deploy_target
from meridian.remnawave import MeridianPanel, NodeCredentials, RemnawaveError
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
# Provisioner pipeline
# ---------------------------------------------------------------------------


def _run_provisioner(
    resolved: ResolvedServer,
    cluster: ClusterConfig,
    domain: str,
    sni: str,
    harden: bool,
    is_panel_host: bool,
    secret_path: str,
    xhttp_port: int,
    reality_port: int,
    wss_port: int,
    *,
    pq: bool = False,
    warp: bool = False,
    geo_block: bool = True,
    xhttp_path: str = "",
    ws_path: str = "",
    info_page_path: str = "",
    reporter: Reporter | None = None,
    operation: OperationContext | None = None,
    remote_executor: RemoteExecutor | None = None,
) -> None:
    """Run the SSH-based provisioner pipeline (OS, Docker, containers)."""
    from meridian.provision import ProvisionContext, Provisioner, build_node_steps, build_setup_steps

    ctx = ProvisionContext(
        ip=resolved.ip,
        user=resolved.user,
        domain=domain,
        sni=sni or DEFAULT_SNI,
        xhttp_enabled=True,
        pq_encryption=pq,
        warp=warp,
        geo_block=geo_block,
        hosted_page=True,
        harden=harden,
        is_panel_host=is_panel_host,
    )
    ctx.xhttp_port = xhttp_port
    ctx.reality_port = reality_port
    ctx.wss_port = wss_port

    # Store cluster and secret path in context for provisioner steps
    ctx.cluster = cluster
    ctx["secret_path"] = secret_path
    # nginx needs these paths for reverse proxy and connection page locations
    ctx["web_base_path"] = secret_path
    ctx["info_page_path"] = info_page_path or cluster.panel.sub_path or secrets.token_hex(8)
    # Provide xhttp/ws paths — reuse saved paths on redeploy, generate fresh otherwise
    ctx["xhttp_path"] = xhttp_path or secrets.token_hex(8)
    ctx["ws_path"] = ws_path or secrets.token_hex(8)
    # Subscription page path for nginx reverse proxy — reuse if already set,
    # otherwise generate. The v4 panel stack always deploys the subscription
    # page container, so we always need a stable path persisted to cluster.yml
    # (without persisting, the next REMOVE_SUBSCRIPTION_PAGE would skip nginx
    # cleanup and a subsequent re-enable would add a duplicate nginx route).
    from meridian.cluster import SubscriptionPageConfig

    sub_page_path = ""
    if cluster.subscription_page and cluster.subscription_page.path:
        sub_page_path = cluster.subscription_page.path
    sub_page_path = sub_page_path or secrets.token_hex(8)
    ctx["subscription_page_path"] = sub_page_path
    if cluster.subscription_page is None:
        cluster.subscription_page = SubscriptionPageConfig(path=sub_page_path)
    else:
        cluster.subscription_page.path = sub_page_path

    if not is_quiet_mode():
        err_console.print()
    info(f"Configuring server at {ctx.ip}...")
    if domain:
        info(f"Domain: {domain}")
    if sni and sni != DEFAULT_SNI:
        info(f"SNI: {sni}")
    if pq:
        info("Post-quantum encryption: enabled (experimental)")
    if warp:
        info("Cloudflare WARP: enabled")
    if not geo_block:
        info("Geo-blocking: disabled (Russian sites accessible)")
    if not is_quiet_mode():
        err_console.print()

    # Choose pipeline: full setup (panel + node) or node-only
    if is_panel_host:
        steps = build_setup_steps(ctx)
    else:
        steps = build_node_steps(ctx)

    provisioner = Provisioner(steps)

    if remote_executor is None and not isinstance(resolved.conn, ServerConnection):
        fail("No SSH connection available", hint_type="bug")
    remote_executor = remote_executor or SSHRemoteExecutor(resolved.conn)
    conn = RemoteExecutorConnection(remote_executor)

    results = provisioner.run(conn, ctx, reporter=reporter, operation=operation, render=not is_quiet_mode())

    # Check for failures
    failed = [r for r in results if r.status == "failed"]
    if failed:
        fail(
            "Setup failed",
            hint=f"Step '{failed[0].name}' failed: {failed[0].detail}\nRun: meridian preflight {ctx.ip}",
            hint_type="system",
        )

    if not is_quiet_mode():
        err_console.print()
    ok("All provisioning steps completed")


# ---------------------------------------------------------------------------
# Post-provisioner: Remnawave API configuration
# ---------------------------------------------------------------------------


def _panel_base_url(ip: str, domain: str, secret_path: str) -> str:
    """Build the panel base URL for API calls.

    Panel runs on 127.0.0.1:3000, reverse-proxied by nginx on the secret path.
    For first deploy before nginx is ready, we use SSH tunnel or direct access.
    Once nginx is up, we use https://<host>/<secret_path>.
    """
    host = domain or ip
    return f"https://{host}/{secret_path}/"


def _create_api_token(base_url: str, auth_token: str) -> str:
    """Create a long-lived API token using the admin auth token.

    Remnawave's auth tokens (from login/register) are short-lived browser
    session tokens. API endpoints require a separate API token created via
    POST /api/tokens with the 'remnawave-client-type: browser' header.
    The API token is effectively permanent (~274 years).
    """
    import httpx

    resp = httpx.post(
        f"{base_url.rstrip('/')}/api/tokens",
        json={"tokenName": "meridian-provisioner"},
        headers={
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
            "X-Remnawave-Client-Type": "browser",
        },
        timeout=30,
        verify=False,
    )
    if resp.status_code not in (200, 201):
        fail(
            f"Could not create API token ({resp.status_code}): {resp.text[:200]}",
            hint="The panel may need a fresh start",
            hint_type="system",
        )
    data = resp.json()
    if isinstance(data, dict) and "response" in data:
        data = data["response"]
    token = data.get("token", "")
    if not token:
        fail("API token creation succeeded but no token returned", hint_type="bug")
    return token


def _generate_reality_keypair(conn: ServerConnection) -> tuple[str, str]:
    """Generate x25519 keypair for Reality using tools available on the server.

    Tries multiple sources: Xray in any running container, xray on host.
    Returns (private_key, public_key) as base64 strings.
    """
    # Try various Xray binaries that might be available
    cmds = [
        "docker exec remnawave-node rw-core x25519 2>/dev/null",
        "docker exec 3x-ui /app/bin/xray-linux-amd64 x25519 2>/dev/null",
        "xray x25519 2>/dev/null",
    ]
    for cmd in cmds:
        result = conn.run(cmd, timeout=15)
        if result.returncode != 0 or not result.stdout.strip():
            continue
        private_key = ""
        public_key = ""
        for raw_line in result.stdout.strip().splitlines():
            ln = raw_line.strip()
            low = ln.lower()
            if "private" in low and ":" in ln:
                private_key = ln.split(":", 1)[1].strip().strip('"')
            elif ("public" in low or "password" in low) and ":" in ln:
                if "hash" not in low:
                    public_key = ln.split(":", 1)[1].strip().strip('"')
        if private_key and public_key:
            return private_key, public_key

    # Last resort: download a temporary Xray binary
    info("Downloading Xray binary for key generation...")
    dl_result = conn.run(
        "ARCH=$(uname -m); "
        'case "$ARCH" in '
        "aarch64|arm64) XRAY_ARCH=arm64-v8a ;; "
        "*) XRAY_ARCH=64 ;; "
        "esac; "
        'curl -sL "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-${XRAY_ARCH}.zip"'
        " -o /tmp/xray.zip && cd /tmp && unzip -qo xray.zip xray && chmod +x xray"
        " && /tmp/xray x25519 && rm -f /tmp/xray /tmp/xray.zip",
        timeout=60,
    )
    if dl_result.returncode == 0:
        for raw_line in dl_result.stdout.strip().splitlines():
            ln = raw_line.strip()
            low = ln.lower()
            if "private" in low and ":" in ln:
                private_key = ln.split(":", 1)[1].strip().strip('"')
            elif ("public" in low or "password" in low) and ":" in ln:
                if "hash" not in low:
                    public_key = ln.split(":", 1)[1].strip().strip('"')
        if private_key and public_key:
            return private_key, public_key

    fail(
        "Could not generate Reality x25519 keypair",
        hint="Install xray on the server or ensure Docker is running",
        hint_type="system",
    )
    return "", ""  # unreachable


def _get_docker_gateway(conn: ServerConnection) -> str:
    """Get the Docker gateway IP for panel-to-node communication.

    When panel (bridge network) and node (host network) are on the same server,
    the panel cannot reach 127.0.0.1 on the host. It must use the gateway IP
    of its Docker network to reach services on the host network.

    We inspect the panel container's actual network, not the default bridge,
    because the panel runs on a custom 'remnawave-net' network.
    """
    result = conn.run(
        "docker inspect remnawave --format '{{range .NetworkSettings.Networks}}{{.Gateway}}{{end}}'",
        timeout=15,
    )
    gateway = result.stdout.strip() if result.returncode == 0 else ""
    return gateway or "172.17.0.1"


def _wait_for_panel_api(base_url: str, retries: int = 20, delay: float = 3.0) -> bool:
    """Wait for the panel REST API to become reachable from the deployer."""
    import httpx

    for attempt in range(retries):
        try:
            resp = httpx.get(
                f"{base_url.rstrip('/')}/api/auth/login",
                timeout=10,
                verify=False,  # Self-signed cert during bootstrap
            )
            # Any response (even 405 Method Not Allowed) means the API is up
            if resp.status_code < 500:
                return True
        except (httpx.ConnectError, httpx.TimeoutException):
            pass
        if attempt < retries - 1:
            time.sleep(delay)
    return False


def _configure_panel_and_node(
    resolved: ResolvedServer,
    cluster: ClusterConfig,
    domain: str,
    sni: str,
    client_name: str,
    is_first_deploy: bool,
    is_redeploy: bool,
    secret_path: str,
    reality_port: int,
    xhttp_port: int,
    wss_port: int,
    *,
    pq: bool = False,
    warp: bool = False,
    geo_block: bool = True,
    xhttp_path: str = "",
    ws_path: str = "",
    info_page_path: str = "",
) -> None:
    """Configure the Remnawave panel via REST API after containers are running.

    For first deploy: register admin, create config profile, register node,
    create hosts, create first client.

    For redeploy: update Xray config, re-register if needed, redeploy container.
    """
    from meridian import __version__

    if not is_quiet_mode():
        err_console.print()
    info("Configuring panel via API...")

    if is_first_deploy:
        _setup_first_deploy(
            resolved=resolved,
            cluster=cluster,
            domain=domain,
            sni=sni,
            client_name=client_name,
            secret_path=secret_path,
            reality_port=reality_port,
            xhttp_port=xhttp_port,
            wss_port=wss_port,
            pq=pq,
            warp=warp,
            geo_block=geo_block,
            version=__version__,
            xhttp_path=xhttp_path,
            ws_path=ws_path,
            info_page_path=info_page_path,
        )
    elif is_redeploy:
        _setup_redeploy(
            resolved=resolved,
            cluster=cluster,
            domain=domain,
            sni=sni,
            reality_port=reality_port,
            xhttp_port=xhttp_port,
            wss_port=wss_port,
            version=__version__,
            pq=pq,
            warp=warp,
            geo_block=geo_block,
            xhttp_path=xhttp_path,
            ws_path=ws_path,
        )
    # Note: new-node path removed — deploy refuses new IPs when cluster
    # is configured. Use `meridian node add` instead.


def _setup_first_deploy(
    resolved: ResolvedServer,
    cluster: ClusterConfig,
    domain: str,
    sni: str,
    client_name: str,
    secret_path: str,
    reality_port: int,
    xhttp_port: int,
    wss_port: int,
    *,
    pq: bool,
    warp: bool = False,
    geo_block: bool,
    version: str,
    xhttp_path: str = "",
    ws_path: str = "",
    info_page_path: str = "",
) -> None:
    """First deploy: full panel bootstrap from scratch."""
    base_url = _panel_base_url(resolved.ip, domain, secret_path)

    # Wait for panel API to become accessible
    info("Waiting for panel API...")
    if not _wait_for_panel_api(base_url):
        fail(
            "Panel API is not reachable",
            hint=(
                f"Panel should be at {base_url}\n"
                f"Check: ssh {resolved.user}@{resolved.ip} docker logs remnawave --tail 30"
            ),
            hint_type="system",
        )

    # Register admin user (reuse saved credentials on re-run after partial failure)
    if cluster.panel.admin_user and cluster.panel.admin_pass:
        admin_user = cluster.panel.admin_user
        admin_pass = cluster.panel.admin_pass
    else:
        admin_user = f"meridian-{secrets.token_hex(4)}"
        # Remnawave requires: ≥24 chars, uppercase + lowercase + numbers
        admin_pass = f"Mx{secrets.token_hex(16)}9A"

    try:
        auth_token = MeridianPanel.register_admin(base_url, admin_user, admin_pass)
    except RemnawaveError as e:
        # Admin may already exist on re-run after partial failure — try login
        try:
            auth_token = MeridianPanel.login(base_url, admin_user, admin_pass)
        except RemnawaveError as login_err:
            fail(
                f"Panel admin registration failed ({e}), login also failed ({login_err})",
                hint="Panel may need a fresh start: meridian teardown, then redeploy",
                hint_type="system",
            )
    ok("Panel admin registered")

    # Save admin credentials BEFORE API token step (lockout prevention:
    # if API token creation fails, we can still login with these creds)
    # Reuse the info_page_path generated for the provisioner (nginx uses it),
    # so the connection page URL matches the nginx location.
    sub_path = info_page_path or secrets.token_hex(8)
    cluster.panel = PanelConfig(
        url=base_url,
        api_token="",  # filled after API token creation
        admin_user=admin_user,
        admin_pass=admin_pass,
        server_ip=resolved.ip,
        ssh_user=resolved.user,
        ssh_port=getattr(resolved.conn, "port", 22),
        secret_path=secret_path,
        sub_path=sub_path,
        deployed_with=version,
    )
    cluster.backup()
    cluster.save()  # Create a long-lived API token (auth token is browser-session only)
    api_token = _create_api_token(base_url, auth_token)
    ok("API token created")

    # Update cluster with the API token
    cluster.panel.api_token = api_token
    cluster.save()

    # Configure subscription page with the real API token
    from meridian.provision.remnawave_panel import configure_subscription_page

    if configure_subscription_page(resolved.conn, api_token):
        from meridian.cluster import SubscriptionPageConfig

        if cluster.subscription_page is None:
            cluster.subscription_page = SubscriptionPageConfig()
        cluster.subscription_page._extra["deployed"] = True
        cluster.save()
    ok("Subscription page configured")

    with MeridianPanel(base_url, api_token) as panel:
        # Create config profile (Xray inbound definitions)
        profile_name = "meridian-default"
        xray_result = _build_xray_config(
            resolved.conn,
            sni=sni,
            reality_port=reality_port,
            xhttp_port=xhttp_port,
            wss_port=wss_port,
            domain=domain,
            pq=pq,
            geo_block=geo_block,
            warp=warp,
            xhttp_path=xhttp_path,
            ws_path=ws_path,
        )
        xray_config = xray_result["config"]
        reality_public_key = xray_result["reality_public_key"]
        reality_short_id = xray_result["reality_short_id"]
        reality_private_key = xray_result["reality_private_key"]

        try:
            existing_profile = panel.find_config_profile_by_name(profile_name)
            if existing_profile:
                profile = existing_profile
                info(f"Config profile '{profile_name}' already exists, reusing")
            else:
                profile = panel.create_config_profile(profile_name, xray_config)
                ok("Config profile created")
            cluster.config_profile_uuid = profile.uuid
            cluster.config_profile_name = profile.name
        except RemnawaveError as e:
            fail(f"Failed to create config profile: {e}", hint_type="system")

        # Cache inbound references from the profile
        _cache_inbounds(panel, cluster)

        # Assign inbounds to Default-Squad so users can access them.
        # Remnawave requires users and inbounds to share an "internal squad"
        # for the user to appear in the node's Xray config.
        try:
            squad_uuid = panel.get_default_squad_uuid()
            if squad_uuid:
                inbound_uuids_all = [
                    ref.uuid for ref in cluster.inbounds.values() if isinstance(ref, InboundRef) and ref.uuid
                ]
                if inbound_uuids_all:
                    panel.assign_inbounds_to_squad(squad_uuid, inbound_uuids_all)
                    ok("Inbounds linked to Default-Squad")
                cluster.squad_uuid = squad_uuid
            else:
                warn("Default-Squad not found — users may not get access to inbounds")
        except RemnawaveError as e:
            warn(f"Could not configure squad: {e}")

        # Register this server as a node
        # For same-server deployments (panel + node co-located), the panel runs
        # in a Docker bridge network and cannot reach the node on 127.0.0.1.
        # Use the Docker bridge gateway IP so the panel can reach the node.
        node_address = _get_docker_gateway(resolved.conn)
        node_name = domain or resolved.ip
        try:
            inbound_uuids = [ref.uuid for ref in cluster.inbounds.values() if isinstance(ref, InboundRef) and ref.uuid]
            existing_api_node = panel.find_node_by_address(node_address)
            if existing_api_node:
                info(f"Node at {node_address} already registered, reusing")
                # Re-fetch keygen for secret key (needed for container .env)
                secret_key = panel.get_node_secret_key()
                node_creds = NodeCredentials(uuid=existing_api_node.uuid, secret_key=secret_key)
            else:
                node_creds = panel.create_node(
                    name=node_name,
                    address=node_address,
                    port=REMNAWAVE_NODE_API_PORT,
                    config_profile_uuid=cluster.config_profile_uuid,
                    inbound_uuids=inbound_uuids,
                )
                ok(f"Node registered: {node_name}")
        except RemnawaveError as e:
            fail(f"Failed to register node: {e}", hint_type="system")

        if not _deploy_node_container(resolved.conn, node_creds.secret_key):
            fail(
                "Node container did not become healthy",
                hint=(
                    f"Check: ssh {shlex.quote(resolved.user)}@{resolved.ip} "
                    "docker logs remnawave-node --tail 50"
                ),
                hint_type="system",
            )

        # Save node entry to cluster
        node_entry = NodeEntry(
            ip=resolved.ip,
            uuid=node_creds.uuid,
            name=node_name,
            ssh_user=resolved.user,
            ssh_port=getattr(resolved.conn, "port", 22),
            sni=sni,
            domain=domain,
            is_panel_host=True,
            deployed_with=version,
            warp=warp,
            xhttp_path=xhttp_path,
            ws_path=ws_path,
            reality_public_key=reality_public_key,
            reality_short_id=reality_short_id,
            reality_private_key=reality_private_key,
        )
        # Deduplicate: update existing entry or append new
        cluster.remove_node(resolved.ip)
        cluster.nodes.append(node_entry)
        cluster.backup()
        cluster.save()

        # Create direct hosts for this node's protocols
        _create_hosts_for_node(panel, cluster, resolved.ip, domain, sni, reality_port)

        # Create first client
        try:
            existing_user = panel.get_user(client_name)
            if existing_user:
                info(f"Client '{client_name}' already exists")
                user = existing_user
            else:
                squad_uuids = [cluster.squad_uuid] if cluster.squad_uuid else None
                user = panel.create_user(client_name, squad_uuids=squad_uuids)
                ok(f"Client '{client_name}' created")

            # Deploy connection page for this client
            if user and isinstance(getattr(user, "vless_uuid", None), str) and user.vless_uuid:
                try:
                    sub_url = panel.get_subscription_url(user.short_uuid) if user.short_uuid else ""
                    page_url = _deploy_client_page(
                        resolved.conn, cluster, node_entry, user.vless_uuid, client_name, sub_url
                    )
                    if page_url:
                        ok("Connection page deployed")
                        cluster._extra["_page_url"] = page_url
                    if sub_url:
                        cluster._extra["_subscription_url"] = sub_url
                except Exception:
                    pass  # Non-fatal — subscription URL still works
        except RemnawaveError as e:
            warn(f"Could not create client '{client_name}': {e}")

    ok("Panel configuration complete")


def _setup_redeploy(
    resolved: ResolvedServer,
    cluster: ClusterConfig,
    domain: str,
    sni: str,
    reality_port: int,
    xhttp_port: int,
    wss_port: int,
    version: str,
    *,
    pq: bool = False,
    warp: bool = False,
    geo_block: bool = True,
    xhttp_path: str = "",
    ws_path: str = "",
) -> None:
    """Redeploy: update node config, rebuild Xray profile, redeploy container.

    Preserves Reality keys from cluster.yml so existing client configs
    continue to work. Re-runs panel API configuration to pick up
    changes to SNI, domain, protocol options, etc.
    """
    if not cluster.panel.api_token:
        fail(
            "Cluster config exists but has no API token",
            hint="Run: meridian teardown, then redeploy from scratch",
            hint_type="system",
        )

    node = cluster.find_node(resolved.ip)
    if not node:
        fail(f"Node {resolved.ip} not found in cluster config", hint_type="system")

    try:
        with MeridianPanel(cluster.panel.url, cluster.panel.api_token) as panel:
            if not panel.ping():
                fail(
                    "Cannot reach panel API",
                    hint=f"Panel URL: {cluster.panel.url}\nCheck panel logs on {cluster.panel.server_ip}",
                    hint_type="system",
                )
            ok("Panel API accessible")

            # Build Xray config reusing existing Reality keys
            # If we have saved keys, skip keygen (preserves client configs)
            if node.reality_private_key and node.reality_public_key and node.reality_short_id:
                info("Reusing existing Reality keys (client configs preserved)")
                xray_result = _build_xray_config(
                    None,  # no SSH needed when reusing keys
                    sni=sni or node.sni,
                    reality_port=reality_port,
                    xhttp_port=xhttp_port,
                    wss_port=wss_port,
                    domain=domain or node.domain,
                    pq=pq,
                    geo_block=geo_block,
                    warp=warp,
                    xhttp_path=xhttp_path or node.xhttp_path,
                    ws_path=ws_path or node.ws_path,
                    existing_private_key=node.reality_private_key,
                    existing_public_key=node.reality_public_key,
                    existing_short_id=node.reality_short_id,
                )
            else:
                # No saved keys — must regenerate (breaks existing client configs)
                warn("No saved Reality keys — regenerating (clients will need new configs)")
                xray_result = _build_xray_config(
                    resolved.conn,
                    sni=sni or node.sni,
                    reality_port=reality_port,
                    xhttp_port=xhttp_port,
                    wss_port=wss_port,
                    domain=domain or node.domain,
                    pq=pq,
                    geo_block=geo_block,
                    warp=warp,
                    xhttp_path=xhttp_path or node.xhttp_path,
                    ws_path=ws_path or node.ws_path,
                )

            xray_config = xray_result["config"]
            reality_public_key = xray_result["reality_public_key"]
            reality_short_id = xray_result["reality_short_id"]
            reality_private_key = xray_result["reality_private_key"]

            # Update or create config profile
            profile_name = cluster.config_profile_name or "meridian-default"
            try:
                existing_profile = panel.find_config_profile_by_name(profile_name)
                if existing_profile:
                    profile = existing_profile
                    info(f"Config profile '{profile_name}' already exists, reusing")
                else:
                    profile = panel.create_config_profile(profile_name, xray_config)
                    ok("Config profile created")
                cluster.config_profile_uuid = profile.uuid
                cluster.config_profile_name = profile.name
            except RemnawaveError as e:
                warn(f"Could not update config profile: {e}")

            # Re-cache inbounds
            _cache_inbounds(panel, cluster)

            # Refresh squad-inbound linkage
            try:
                squad_uuid = cluster.squad_uuid or panel.get_default_squad_uuid()
                if squad_uuid:
                    inbound_uuids_all = [
                        ref.uuid for ref in cluster.inbounds.values() if isinstance(ref, InboundRef) and ref.uuid
                    ]
                    if inbound_uuids_all:
                        panel.assign_inbounds_to_squad(squad_uuid, inbound_uuids_all)
                    cluster.squad_uuid = squad_uuid
            except RemnawaveError:
                pass  # Non-fatal on redeploy

            # Verify node registration
            if node.uuid:
                api_node = panel.get_node(node.uuid)
                if api_node:
                    ok(f"Node {resolved.ip} still registered")
                else:
                    warn(f"Node {resolved.ip} not found in panel — re-registering")
                    inbound_uuids = [
                        ref.uuid for ref in cluster.inbounds.values() if isinstance(ref, InboundRef) and ref.uuid
                    ]
                    # Panel-host nodes must use Docker gateway address (panel
                    # runs in bridge network, can't reach 127.0.0.1 on host).
                    if node.is_panel_host:
                        node_address = _get_docker_gateway(resolved.conn)
                    else:
                        node_address = resolved.ip
                    node_creds = panel.create_node(
                        name=domain or resolved.ip,
                        address=node_address,
                        port=REMNAWAVE_NODE_API_PORT,
                        config_profile_uuid=cluster.config_profile_uuid,
                        inbound_uuids=inbound_uuids,
                    )
                    node.uuid = node_creds.uuid
                    if not _deploy_node_container(resolved.conn, node_creds.secret_key):
                        fail(
                            "Node container did not become healthy",
                            hint=(
                                f"Check: ssh {shlex.quote(resolved.user)}@{resolved.ip} "
                                "docker logs remnawave-node --tail 50"
                            ),
                            hint_type="system",
                        )
            else:
                warn("Node has no UUID — skipping panel verification")

            # Redeploy node container (refresh secret key + image)
            if node.uuid:
                secret_key = panel.get_node_secret_key()
                if secret_key:
                    if not _deploy_node_container(resolved.conn, secret_key):
                        fail(
                            "Node container did not become healthy",
                            hint=(
                                f"Check: ssh {shlex.quote(resolved.user)}@{resolved.ip} "
                                "docker logs remnawave-node --tail 50"
                            ),
                            hint_type="system",
                        )

            # Recreate hosts (idempotent)
            # Use provided values; only fall back to stored values if caller passed ""
            # (which means "not specified" from imperative commands, or "clear" from apply)
            effective_domain = domain if domain else node.domain
            effective_sni = sni if sni else node.sni
            _create_hosts_for_node(panel, cluster, resolved.ip, effective_domain, effective_sni, reality_port)

            # Update node metadata
            # For sni/domain: empty string from declarative apply means "clear",
            # non-empty means "set". Imperative commands always pass the resolved value.
            if sni:
                node.sni = sni
            if domain:
                node.domain = domain
            node.deployed_with = version
            node.warp = warp
            node.reality_public_key = reality_public_key
            node.reality_short_id = reality_short_id
            node.reality_private_key = reality_private_key
            node.xhttp_path = xhttp_path or node.xhttp_path
            node.ws_path = ws_path or node.ws_path
            cluster.backup()
            cluster.save()
            ok("Node configuration updated")

            # Ensure subscription page has a valid token (upgrade from pre-subscription deploys)
            if node.is_panel_host:
                from meridian.provision.remnawave_panel import configure_subscription_page

                if configure_subscription_page(resolved.conn, cluster.panel.api_token):
                    from meridian.cluster import SubscriptionPageConfig

                    if cluster.subscription_page is None:
                        cluster.subscription_page = SubscriptionPageConfig()
                    cluster.subscription_page._extra["deployed"] = True
                    cluster.save()

    except RemnawaveError as e:
        fail(f"Panel API error: {e}", hint_type="system")


def _setup_new_node(
    resolved: ResolvedServer,
    cluster: ClusterConfig,
    domain: str,
    sni: str,
    reality_port: int,
    xhttp_port: int,
    wss_port: int,
    version: str,
    *,
    xhttp_path: str = "",
    ws_path: str = "",
) -> None:
    """Add a new node to an existing cluster."""
    if not cluster.panel.api_token:
        fail(
            "No panel configured -- deploy a panel first with: meridian deploy <IP>",
            hint_type="user",
        )

    try:
        with MeridianPanel(cluster.panel.url, cluster.panel.api_token) as panel:
            if not panel.ping():
                fail(
                    "Cannot reach panel API",
                    hint=f"Panel URL: {cluster.panel.url}\nCheck panel logs on {cluster.panel.server_ip}",
                    hint_type="system",
                )

            node_name = domain or resolved.ip
            inbound_uuids = [ref.uuid for ref in cluster.inbounds.values() if isinstance(ref, InboundRef) and ref.uuid]
            existing_api_node = panel.find_node_by_address(resolved.ip)
            if existing_api_node:
                info(f"Node at {resolved.ip} already registered, reusing")
                secret_key = panel.get_node_secret_key()
                node_creds = NodeCredentials(uuid=existing_api_node.uuid, secret_key=secret_key)
            else:
                node_creds = panel.create_node(
                    name=node_name,
                    address=resolved.ip,
                    port=REMNAWAVE_NODE_API_PORT,
                    config_profile_uuid=cluster.config_profile_uuid,
                    inbound_uuids=inbound_uuids,
                )
                ok(f"Node registered: {node_name}")

            # Deploy the node container with the secret key
            _deploy_node_container(resolved.conn, node_creds.secret_key)

            # Save node entry
            # Reuse Reality keys from existing node (shared config profile)
            existing_node = cluster.panel_node
            node_entry = NodeEntry(
                ip=resolved.ip,
                uuid=node_creds.uuid,
                name=node_name,
                ssh_user=resolved.user,
                ssh_port=getattr(resolved.conn, "port", 22),
                sni=sni,
                domain=domain,
                is_panel_host=False,
                deployed_with=version,
                xhttp_path=xhttp_path,
                ws_path=ws_path,
                reality_public_key=existing_node.reality_public_key if existing_node else "",
                reality_short_id=existing_node.reality_short_id if existing_node else "",
            )
            cluster.remove_node(resolved.ip)
            cluster.nodes.append(node_entry)
            cluster.backup()
            cluster.save()

            # Create hosts for the new node
            _create_hosts_for_node(panel, cluster, resolved.ip, domain, sni, reality_port)

    except RemnawaveError as e:
        fail(f"Panel API error: {e}", hint_type="system")

    ok("New node configured")


# ---------------------------------------------------------------------------
# Panel API helpers
# ---------------------------------------------------------------------------


def _build_xray_config(
    conn: ServerConnection | None,
    sni: str,
    reality_port: int,
    xhttp_port: int,
    wss_port: int,
    domain: str,
    *,
    pq: bool,
    geo_block: bool,
    warp: bool = False,
    xhttp_path: str = "",
    ws_path: str = "",
    existing_private_key: str = "",
    existing_public_key: str = "",
    existing_short_id: str = "",
) -> dict:
    """Build the Xray configuration for a Remnawave config profile.

    This defines the inbounds that the node will run. The actual Xray
    config is managed by Remnawave, but we provide the template.

    If existing_private_key/public_key/short_id are provided, reuses
    them instead of generating new Reality keys (preserves client configs
    on redeploy). When provided, conn may be None.

    Returns a dict with keys:
      - "config": the Xray config dict
      - "reality_public_key": the Reality public key
      - "reality_short_id": the Reality short ID
    """
    # Base config with DNS and routing
    config: dict = {
        "log": {"loglevel": "warning"},
        "dns": {
            "servers": [
                {"address": "https://dns.google/dns-query", "domains": ["geosite:geolocation-!cn"]},
                "8.8.8.8",
            ],
        },
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": [],
        },
        "inbounds": [],
    }

    # Geo-blocking rules
    if geo_block:
        config["routing"]["rules"].extend(
            [
                {"type": "field", "domain": ["geosite:category-ru"], "outboundTag": "block"},
                {"type": "field", "ip": ["geoip:ru"], "outboundTag": "block"},
            ]
        )

    # Block private IPs (prevent probing internal network)
    config["routing"]["rules"].append({"type": "field", "ip": ["geoip:private"], "outboundTag": "block"})

    # Outbounds
    outbounds: list[dict] = []
    if warp:
        from meridian.provision.warp import WARP_PROXY_PORT

        outbounds.append(
            {
                "tag": "warp",
                "protocol": "socks",
                "settings": {"servers": [{"address": "127.0.0.1", "port": WARP_PROXY_PORT}]},
            }
        )
    outbounds.append({"tag": "direct", "protocol": "freedom"})
    outbounds.append({"tag": "block", "protocol": "blackhole"})
    config["outbounds"] = outbounds

    # Reality inbound (primary -- always present)
    # Reuse existing keys on redeploy, generate fresh on first deploy
    if existing_private_key and existing_public_key and existing_short_id:
        private_key = existing_private_key
        public_key = existing_public_key
        short_id = existing_short_id
    else:
        if conn is None:
            fail("Cannot generate Reality keys without SSH connection", hint_type="bug")
        private_key, public_key = _generate_reality_keypair(conn)
        short_id = secrets.token_hex(4)  # 8-char hex

    reality_inbound = {
        "tag": "vless-reality",
        "protocol": "vless",
        "listen": "127.0.0.1",
        "port": reality_port,
        "settings": {
            "clients": [],
            "decryption": "none",
        },
        "streamSettings": {
            "network": "tcp",
            "security": "reality",
            "realitySettings": {
                "dest": f"{sni}:443",
                "serverNames": [sni],
                "privateKey": private_key,
                "shortIds": [short_id],
                "fingerprint": "chrome",
            },
        },
    }
    if pq:
        rs = reality_inbound["streamSettings"]
        if isinstance(rs, dict):
            rs_inner = rs.get("realitySettings")
            if isinstance(rs_inner, dict):
                rs_inner["fingerprint"] = "chrome"
    config["inbounds"].append(reality_inbound)

    # XHTTP inbound (enhanced stealth -- behind nginx reverse proxy)
    xhttp_stream: dict[str, Any] = {
        "network": "xhttp",
        "security": "none",
    }
    if xhttp_path:
        xhttp_stream["xhttpSettings"] = {"path": f"/{xhttp_path}"}
    xhttp_inbound = {
        "tag": "vless-xhttp",
        "protocol": "vless",
        "listen": "127.0.0.1",
        "port": xhttp_port,
        "settings": {
            "clients": [],
            "decryption": "none",
        },
        "streamSettings": xhttp_stream,
    }
    config["inbounds"].append(xhttp_inbound)

    # WSS inbound (CDN fallback -- domain mode only)
    if domain:
        ws_stream: dict[str, Any] = {
            "network": "ws",
            "security": "none",
        }
        if ws_path:
            ws_stream["wsSettings"] = {"path": f"/{ws_path}"}
        wss_inbound = {
            "tag": "vless-wss",
            "protocol": "vless",
            "listen": "127.0.0.1",
            "port": wss_port,
            "settings": {
                "clients": [],
                "decryption": "none",
            },
            "streamSettings": ws_stream,
        }
        config["inbounds"].append(wss_inbound)

    return {
        "config": config,
        "reality_public_key": public_key,
        "reality_short_id": short_id,
        "reality_private_key": private_key,
    }


def _cache_inbounds(panel: MeridianPanel, cluster: ClusterConfig) -> None:
    """Fetch inbound definitions from the panel and cache their UUIDs."""
    try:
        inbounds = panel.list_inbounds()
        tag_map = {
            "vless-reality": ProtocolKey.REALITY,
            "vless-xhttp": ProtocolKey.XHTTP,
            "vless-wss": ProtocolKey.WSS,
        }
        for ib in inbounds:
            key = tag_map.get(ib.tag)
            if key:
                cluster.inbounds[str(key)] = InboundRef(uuid=ib.uuid, tag=ib.tag)
        ok(f"Cached {len(cluster.inbounds)} inbound references")
    except RemnawaveError as e:
        warn(f"Could not cache inbound references: {e}")


def _create_hosts_for_node(
    panel: MeridianPanel,
    cluster: ClusterConfig,
    node_ip: str,
    domain: str,
    sni: str,
    reality_port: int,
) -> None:
    """Create direct host entries for a node's protocols."""
    host_address = domain or node_ip

    # Batch-fetch all hosts once instead of per-protocol find_host_by_remark
    try:
        existing_remarks = {h.remark for h in panel.list_hosts()}
    except RemnawaveError:
        existing_remarks = set()

    # Reality host (direct IP, port 443 or computed)
    reality_ref = cluster.get_inbound(ProtocolKey.REALITY)
    if reality_ref and reality_ref.uuid:
        remark = f"reality-{node_ip}"
        if remark in existing_remarks:
            info(f"Host '{remark}' already exists, skipping")
        else:
            try:
                panel.create_host(
                    remark=remark,
                    address=node_ip,
                    port=reality_port,
                    config_profile_uuid=cluster.config_profile_uuid,
                    inbound_uuid=reality_ref.uuid,
                    sni=sni,
                    fingerprint="chrome",
                    security_layer="DEFAULT",
                )
                ok(f"Host created: Reality via {node_ip}:{reality_port}")
            except RemnawaveError as e:
                warn(f"Could not create Reality host: {e}")

    # XHTTP host (via domain or IP, port 443 through nginx)
    xhttp_ref = cluster.get_inbound(ProtocolKey.XHTTP)
    if xhttp_ref and xhttp_ref.uuid:
        remark = f"xhttp-{host_address}"
        if remark in existing_remarks:
            info(f"Host '{remark}' already exists, skipping")
        else:
            try:
                panel.create_host(
                    remark=remark,
                    address=host_address,
                    port=443,
                    config_profile_uuid=cluster.config_profile_uuid,
                    inbound_uuid=xhttp_ref.uuid,
                    security_layer="TLS",
                )
                ok(f"Host created: XHTTP via {host_address}:443")
            except RemnawaveError as e:
                warn(f"Could not create XHTTP host: {e}")

    # WSS host (domain mode only, port 443 through CDN)
    if domain:
        wss_ref = cluster.get_inbound(ProtocolKey.WSS)
        if wss_ref and wss_ref.uuid:
            remark = f"wss-{domain}"
            if remark in existing_remarks:
                info(f"Host '{remark}' already exists, skipping")
            else:
                try:
                    panel.create_host(
                        remark=remark,
                        address=domain,
                        port=443,
                        config_profile_uuid=cluster.config_profile_uuid,
                        inbound_uuid=wss_ref.uuid,
                        security_layer="TLS",
                    )
                    ok(f"Host created: WSS via {domain}:443")
                except RemnawaveError as e:
                    warn(f"Could not create WSS host: {e}")


def _deploy_node_container(conn: ServerConnection, secret_key: str) -> bool:
    """Deploy the Remnawave node container with the given secret key.

    Creates /opt/remnanode, writes docker-compose.yml and .env, pulls the
    image, and starts the container. Called from the post-provisioner phase
    after the panel API has registered the node and returned the mTLS secret.
    """
    from meridian.config import REMNAWAVE_NODE_API_PORT, REMNAWAVE_NODE_DIR, REMNAWAVE_NODE_IMAGE
    from meridian.provision.remnawave_node import _render_node_compose, _render_node_env

    node_dir = REMNAWAVE_NODE_DIR
    q_dir = shlex.quote(node_dir)

    # Create directory
    result = conn.run(f"mkdir -p {q_dir} && chmod 700 {q_dir}", timeout=15)
    if result.returncode != 0:
        warn(f"Could not create {node_dir}: {result.stderr.strip()[:200]}")
        return False

    # Write .env
    env_content = _render_node_env(REMNAWAVE_NODE_API_PORT, secret_key)
    env_path = f"{node_dir}/.env"
    result = conn.put_text(
        env_path,
        env_content,
        mode="600",
        sensitive=True,
        timeout=15,
        operation_name="write remnawave node env",
    )
    if result.returncode != 0:
        warn(f"Could not write {env_path}: {result.stderr.strip()[:200]}")
        return False

    # Write docker-compose.yml
    compose_content = _render_node_compose(REMNAWAVE_NODE_IMAGE, REMNAWAVE_NODE_API_PORT)
    compose_path = f"{node_dir}/docker-compose.yml"
    result = conn.put_text(
        compose_path,
        compose_content,
        mode="644",
        timeout=15,
        operation_name="write remnawave node compose",
    )
    if result.returncode != 0:
        warn(f"Could not write {compose_path}: {result.stderr.strip()[:200]}")
        return False

    # Pull image
    info("Pulling Remnawave node image...")
    result = conn.run(
        "docker compose pull",
        cwd=node_dir,
        timeout=300,
        retries=3,
        retry_delay=10,
        operation_name="pull remnawave node image",
    )
    if result.returncode != 0:
        warn("Could not pull node image — node may not start")
        return False

    # Start container
    result = conn.run("docker compose up -d", cwd=node_dir, timeout=120)
    if result.returncode != 0:
        warn(f"Node container failed to start: {result.stderr.strip()[:200]}")
        return False

    ok("Remnawave node deployed")

    # Health gate: verify the node container started
    info("Verifying node container health...")
    node_healthy = False
    for _attempt in range(10):
        check = conn.run(
            "docker inspect remnawave-node --format '{{.State.Running}}' 2>/dev/null",
            timeout=10,
        )
        if check.returncode == 0 and "true" in check.stdout.strip().lower():
            node_healthy = True
            break
        time.sleep(3)

    if node_healthy:
        ok("Node container verified healthy")
    else:
        logs = conn.run("docker logs remnawave-node --tail 20 2>&1", timeout=15)
        log_tail = logs.stdout.strip()[:500] if logs.returncode == 0 else "(no logs available)"
        warn(
            f"Node container may not be healthy after 30s\n"
            f"  Recent logs:\n{log_tail}\n"
            f"  Check: ssh {shlex.quote(conn.user)}@{conn.ip} docker logs remnawave-node"
        )

    # Allow Docker internal traffic to reach the node API port
    # (panel in bridge network → node on host via gateway IP)
    from meridian.config import REMNAWAVE_NODE_API_PORT

    conn.run(
        f"ufw allow from 172.16.0.0/12 to any port {REMNAWAVE_NODE_API_PORT} proto tcp"
        f" comment 'Meridian node API (Docker internal)' 2>/dev/null; true",
        timeout=15,
    )

    return node_healthy


# ---------------------------------------------------------------------------
# Connection page deployment
# ---------------------------------------------------------------------------


def _deploy_client_page(
    conn: ServerConnection,
    cluster: ClusterConfig,
    node: NodeEntry,
    user_uuid: str,
    client_name: str,
    sub_url: str = "",
) -> str:
    """Generate and upload a PWA connection page for a client.

    Builds VLESS protocol URLs from cluster.yml node data + client UUID,
    generates QR codes and PWA files, uploads to the server.

    Returns the page URL on success, empty string on failure.
    """
    from meridian.models import ProtocolURL
    from meridian.protocols import PROTOCOLS
    from meridian.pwa import generate_client_files, upload_client_files
    from meridian.urls import generate_qr_base64

    host = node.domain or node.ip
    info_page_path = cluster.panel.sub_path or ""
    if not info_page_path:
        return ""

    page_url = f"https://{host}/{info_page_path}/{user_uuid}/"

    # Build protocol URLs using the protocol registry's build_url() methods
    protocol_urls: list[ProtocolURL] = []

    # Reality (always)
    if node.reality_public_key:
        reality = PROTOCOLS.get("reality")
        if reality:
            url = reality.build_url(
                user_uuid,
                client_name,
                ip=node.ip,
                sni=node.sni,
                public_key=node.reality_public_key,
                short_id=node.reality_short_id or "",
                server_name=cluster.branding.server_name,
            )
            qr = generate_qr_base64(url)
            protocol_urls.append(ProtocolURL(key="reality", label=reality.display_label, url=url, qr_b64=qr))

    # XHTTP (if path configured)
    if node.xhttp_path:
        xhttp = PROTOCOLS.get("xhttp")
        if xhttp:
            url = xhttp.build_url(
                user_uuid,
                client_name,
                ip=node.ip,
                xhttp_path=node.xhttp_path,
                domain=node.domain or "",
                server_name=cluster.branding.server_name,
            )
            qr = generate_qr_base64(url)
            protocol_urls.append(ProtocolURL(key="xhttp", label=xhttp.display_label, url=url, qr_b64=qr))

    # WSS (only in domain mode)
    if node.domain and node.ws_path:
        wss = PROTOCOLS.get("wss")
        if wss:
            url = wss.build_url(
                user_uuid,
                client_name,
                domain=node.domain,
                ws_path=node.ws_path,
                server_name=cluster.branding.server_name,
            )
            qr = generate_qr_base64(url)
            protocol_urls.append(ProtocolURL(key="wss", label=wss.display_label, url=url, qr_b64=qr))

    if not protocol_urls:
        return ""

    files = generate_client_files(
        protocol_urls,
        server_ip=node.ip,
        domain=node.domain or "",
        client_name=client_name,
        server_name=cluster.branding.server_name,
        server_icon=cluster.branding.icon,
        color=cluster.branding.color,
        page_url=page_url,
    )

    error = upload_client_files(conn, user_uuid, files)
    if error:
        warn(f"Could not deploy connection page: {error}")
        return ""

    return page_url


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
