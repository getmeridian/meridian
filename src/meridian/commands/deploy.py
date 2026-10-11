"""`meridian deploy` — one command from a bare server to shareable links.

Builds a single-server topology intent (the same model `meridian setup`
produces) and runs the reviewed apply, so a one-command deploy and a guided
multi-server setup converge, verify, and hand off identically.
"""

from __future__ import annotations

import shlex
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from meridian.adapters import JsonlReporter
from meridian.cluster import BrandingConfig, ClusterConfig
from meridian.commands._helpers import ReviewedApplyPersistenceError, persist_reviewed_apply
from meridian.commands.client_handoff import print_handoff_links
from meridian.commands.port_check import check_ports
from meridian.commands.resolve import ensure_server_connection, resolve_server
from meridian.compiler import compile_topology
from meridian.compiler.models import ConfigProfilePayload
from meridian.config import SERVER_PROFILES_FILE, is_ip
from meridian.console import (
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
from meridian.core.defaults import DEFAULT_SNI, REALITY_SNI_CANDIDATES
from meridian.core.deploy import DeployRequest, DeployResult, build_deploy_workflow
from meridian.core.deploy_planning import DeployPlan, DeployPorts
from meridian.core.deploy_validation import DeployValidationError, normalize_deploy_request
from meridian.core.errors import EngineError, MeridianError
from meridian.core.events import COMMAND_COMPLETED, COMMAND_STARTED
from meridian.core.inputs import is_local_deploy_target
from meridian.core.models import OutputStatus, Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.reporters import NoopReporter, Reporter, emit_event
from meridian.core.setup import SetupDraft
from meridian.core.topology import SetupIntent
from meridian.core.validation import wrap_validation_error
from meridian.engine.deploy import resolve_deploy_target
from meridian.handoff import HandoffLinks
from meridian.renderers import emit_json
from meridian.servers import ServerEntry, ServerRegistry
from meridian.setup.single_server import DEFAULT_CLIENT, single_server_intent
from meridian.ssh import ServerConnection

_PENDING_SERVER_REF = "srv-pending"


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
    hysteria2: bool = False,
    ssh_port: int = 22,
    request_path: str = "",
    json_output: bool = False,
    events: str = "",
    dry_run: bool = False,
) -> None:
    """Deploy (or redeploy) one server and hand off its first client."""
    operation = OperationContext()
    final_json = json_output or events == "jsonl"
    reporter: Reporter = JsonlReporter() if events == "jsonl" else NoopReporter()

    with error_context("deploy", timer=operation.timer), _quiet_machine_output(final_json):
        if events and events != "jsonl":
            fail(f"Unsupported --events value: {events}", hint="Use: --events=jsonl", hint_type="user")
        request = (
            _load_request(request_path)
            if request_path
            else _build_request(
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
                hysteria2=hysteria2,
                ssh_port=ssh_port,
            )
        )
        machine_mode = final_json or bool(request_path) or dry_run
        if build_deploy_workflow(request).needs_input:
            if machine_mode:
                fail(
                    "Deploy request is incomplete",
                    hint="Pass a server IP, or call `meridian api workflow deploy --json` and use --request FILE.",
                    hint_type="user",
                )
            request = _build_request(**{**request.model_dump(), "ip": _prompt_server_ip()})
        try:
            request = normalize_deploy_request(request)
        except DeployValidationError as exc:
            fail(str(exc), hint=exc.hint, hint_type="user")

        emit_event(
            reporter,
            operation,
            COMMAND_STARTED,
            phase="deploy",
            message="Deploy dry-run started" if dry_run else "Deploy started",
            data={"command": "deploy", "dry_run": dry_run},
        )
        if dry_run:
            plan = _dry_run(request)
            _emit_completed(reporter, operation, mode=plan.mode, server_ip=plan.server_ip, dry_run=True)
            if final_json:
                _emit_json(plan, operation=operation, status="ok")
            else:
                err_console.print(f"\n  [bold]Deploy dry-run:[/bold] {plan.mode} for {plan.server_ip}\n")
            return

        if machine_mode and not request.yes:
            fail(
                "Machine deploy requires confirmation in the request",
                hint="Set `yes: true` after the UI user confirms the deployment.",
                hint_type="user",
            )
        result = _execute(request)
        _emit_completed(reporter, operation, mode=result.mode, server_ip=result.server_ip, dry_run=False)
        if final_json:
            _emit_json(result, operation=operation, status="changed")


def _execute(request: DeployRequest) -> DeployResult:
    registry = ServerRegistry(SERVER_PROFILES_FILE)
    try:
        target = resolve_deploy_target(request, registry)
    except EngineError as exc:
        fail(str(exc), hint=exc.hint, hint_type=exc.category)
    resolved = ensure_server_connection(
        resolve_server(registry, explicit_ip=target.server_ip, user=target.ssh_user, port=target.ssh_port)
    )

    cluster = ClusterConfig.load()
    _refuse_pre_topology_cluster(cluster)
    first_deploy = cluster.topology_intent is None
    if first_deploy:
        check_ports(resolved.conn, resolved.ip, request.yes)

    registry.add(ServerEntry(host=resolved.ip, user=resolved.user, port=getattr(resolved.conn, "port", 22)))
    entry = registry.find(resolved.ip)
    if entry is None or not entry.id:
        fail(
            "Could not save the server profile", hint="Check ~/.meridian/servers.json permissions.", hint_type="system"
        )
    # Without --sni, a fresh deploy tries known-good camouflage targets in order;
    # redeploys keep the saved target unless --sni changes it.
    if request.sni or not first_deploy:
        sni_candidates = [request.sni]
    else:
        sni_candidates = _reachable_reality_targets(resolved.conn)

    from meridian.setup.runtime import SetupRuntime

    def connect(candidate: ServerEntry) -> ServerConnection:
        # Reuse the verified (possibly local-mode) connection for the deploy target.
        if candidate.id == entry.id:
            return resolved.conn
        return ServerConnection(candidate.host, candidate.user, port=candidate.port, identity_file=candidate.key_path)

    runtime = SetupRuntime(registry, persist=persist_reviewed_apply, connection_builder=connect)
    info("First deployment: installing the control plane and proxy" if first_deploy else "Redeploying")
    info("Applying the reviewed topology; this takes a few minutes on a fresh server...")
    client = request.client_name or DEFAULT_CLIENT
    try:
        for attempt, sni in enumerate(sni_candidates):
            intent = _intent_or_fail(
                cluster.topology_intent if attempt == 0 else ClusterConfig.load().topology_intent,
                entry.id,
                _build_request(**{**request.model_dump(), "sni": sni}),
            )
            applied = runtime.apply_intent(intent)
            if not applied.all_succeeded:
                failures = "; ".join(f"{item.action.resource.logical_id}: {item.error}" for item in applied.failed)
                fail(
                    "Deploy did not converge",
                    hint=f"{failures}. Fix the cause and run the same command again; finished steps are skipped.",
                    hint_type="system",
                )
            ok("Server converged")
            draft = SetupDraft.from_intent(intent).model_copy(
                update={"review_hash": applied.plan_hash, "applied_plan_hash": applied.plan_hash}
            )
            runtime.verify(draft)
            ok("Verified the control plane, node, and subscription")
            traffic, detail = _verify_traffic(resolved.ip, client)
            if traffic == "failed" and attempt + 1 < len(sni_candidates):
                warn(f"No traffic through camouflage target {sni or DEFAULT_SNI}; trying {sni_candidates[attempt + 1]}")
                continue
            _report_traffic(traffic, detail, resolved.ip)
            break
        _save_branding(request)
        (links,) = runtime.publish_handoff([client])
    except ReviewedApplyPersistenceError as exc:
        fail("Deploy changed the server, but local state could not be saved", hint=str(exc), hint_type="system")
    except MeridianError as exc:
        fail(exc)

    cluster = ClusterConfig.load()
    _print_success(resolved.ip, cluster, request, links)
    exit_ = next(item for item in intent.exits if item.server_ref == entry.id)
    reality = next(path for path in exit_.paths if path.protocol == "reality")
    return DeployResult(
        mode="first_deploy" if first_deploy else "redeploy",
        server_ip=resolved.ip,
        ssh_user=resolved.user,
        ssh_port=getattr(resolved.conn, "port", target.ssh_port),
        domain=request.domain,
        sni=reality.reality_sni,
        client_name=client,
        harden=request.harden,
        warp=request.warp,
        geo_block=request.geo_block,
        panel_url=cluster.panel.url,
        panel_secret_path=cluster.panel.secret_path,
        connection_page_path=cluster.panel.sub_path,
        connection_page_url=links.page_url,
        subscription_url=links.subscription_url,
        test_command=f"meridian test {resolved.ip}",
        node_count=len(intent.exits),
        relay_count=len(intent.transparent_relays),
        summary=f"Deploy completed for {resolved.ip}",
    )


TrafficOutcome = Literal["ok", "egress_mismatch", "failed", "inconclusive"]


def _reachable_reality_targets(conn: ServerConnection) -> list[str]:
    """Candidate camouflage targets the server itself can reach over TLS 1.3 + HTTP/2."""
    reachable = []
    for candidate in REALITY_SNI_CANDIDATES:
        probe = conn.run(
            f"curl -s -o /dev/null --max-time 8 --tlsv1.3 --http2 -w '%{{http_version}}' "
            f"https://{shlex.quote(candidate)}/",
            timeout=15,
        )
        if probe.returncode == 0 and probe.stdout.strip() == "2":
            reachable.append(candidate)
    if not reachable:
        fail(
            "The server cannot reach any Reality camouflage target over TLS 1.3",
            hint=(
                f"Tried {', '.join(REALITY_SNI_CANDIDATES)}. "
                "Check outbound HTTPS from the server, or pass --sni (see `meridian scan`)."
            ),
            hint_type="system",
        )
    return reachable


def _verify_traffic(server_ip: str, client: str) -> tuple[TrafficOutcome, str]:
    """Push real traffic through the delivered subscription, as `meridian test` does."""
    from meridian.commands.test import collect_test_result

    try:
        result = collect_test_result(server_ip, "", "", "", client=client, timeout=8)
    except MeridianError as exc:
        return "inconclusive", str(exc)
    findings = [
        finding for check in result.checks for finding in check.findings if finding.code.startswith("PROTOCOL_")
    ]
    by_code = {finding.code: finding.message for finding in findings}
    for code in ("PROTOCOL_TRAFFIC_FAILED", "PROTOCOL_CONFIG_INVALID"):
        if code in by_code:
            return "failed", by_code[code]
    if "PROTOCOL_EGRESS_MISMATCH" in by_code:
        return "egress_mismatch", by_code["PROTOCOL_EGRESS_MISMATCH"]
    if "PROTOCOL_TRAFFIC_OK" in by_code:
        return "ok", by_code["PROTOCOL_TRAFFIC_OK"]
    return "inconclusive", next(iter(by_code.values()), "no traffic evidence was collected")


def _report_traffic(outcome: TrafficOutcome, detail: str, server_ip: str) -> None:
    if outcome == "ok":
        ok("Traffic verified end to end through the delivered subscription")
    elif outcome == "egress_mismatch":
        warn(f"Traffic works, but leaves through an unexpected address: {detail}")
    elif outcome == "inconclusive":
        warn(f"Could not verify traffic ({detail}). Check later with: meridian test {server_ip}")
    else:
        fail(
            "The server deployed, but no traffic passes through it",
            hint=f"{detail}. Try another camouflage target with --sni (see `meridian scan`).",
            hint_type="system",
        )


def _dry_run(request: DeployRequest) -> DeployPlan:
    """Compile the intent a deploy would apply, without SSH or local writes."""
    registry = ServerRegistry(SERVER_PROFILES_FILE)
    try:
        target = resolve_deploy_target(request, registry)
    except EngineError as exc:
        fail(str(exc), hint=exc.hint, hint_type=exc.category)
    cluster = ClusterConfig.load()
    _refuse_pre_topology_cluster(cluster)
    entry = registry.find(target.server_ip)
    server_ref = entry.id if entry and entry.id else _PENDING_SERVER_REF
    intent = _intent_or_fail(cluster.topology_intent, server_ref, request)
    plan = compile_topology(intent)
    ports = {
        inbound.protocol: inbound.listen_port
        for resource in plan.resources
        if isinstance(resource.payload, ConfigProfilePayload) and resource.payload.workload_id
        for inbound in resource.payload.inbounds
    }
    paths = {path.protocol: path.path for item in intent.exits if item.server_ref == server_ref for path in item.paths}
    return DeployPlan(
        mode="first_deploy" if cluster.topology_intent is None else "redeploy",
        server_ip=target.server_ip,
        ports=DeployPorts(
            reality_port=ports.get("reality", 0),
            xhttp_port=ports.get("xhttp", 0),
            wss_port=ports.get("wss", 0),
        ),
        secret_path=cluster.panel.secret_path,
        xhttp_path=paths.get("xhttp", ""),
        ws_path=paths.get("wss", ""),
        info_page_path=cluster.panel.sub_path,
        node_count=len(intent.exits),
        relay_count=len(intent.transparent_relays),
    )


def _intent_or_fail(existing: SetupIntent | None, server_ref: str, request: DeployRequest) -> SetupIntent:
    try:
        return single_server_intent(
            existing,
            server_ref=server_ref,
            sni=request.sni,
            domain=request.domain,
            client_name=request.client_name,
            warp=request.warp,
            geo_block=request.geo_block,
            hysteria2=request.hysteria2,
        ).model_copy(update={"harden": request.harden})
    except MeridianError as exc:
        fail(exc)


def _refuse_pre_topology_cluster(cluster: ClusterConfig) -> None:
    if cluster.is_configured and cluster.topology_intent is None:
        fail(
            "This cluster.yml was written by a Meridian 4.0 development build",
            hint="Move ~/.meridian/cluster.yml aside, then deploy again to rebuild it from scratch.",
            hint_type="user",
        )


def _save_branding(request: DeployRequest) -> None:
    if not (request.server_name or request.icon or request.color):
        return
    cluster = ClusterConfig.load()
    cluster.branding = BrandingConfig(server_name=request.server_name, icon=request.icon, color=request.color)
    persist_reviewed_apply(cluster)


def _prompt_server_ip() -> str:
    err_console.print("\n  [bold]Deploy Meridian[/bold]")
    err_console.print("  [dim]You need a fresh VPS (Ubuntu/Debian) you can SSH into as root or a sudo user.[/dim]")
    err_console.print("  [dim]For multiple servers, relays, or routing, use: meridian setup[/dim]\n")
    for _attempt in range(3):
        value = prompt("Server IP address").strip()
        if is_ip(value) or is_local_deploy_target(value):
            return value
        err_console.print("  [warn]Enter an IPv4/IPv6 address, or 'local' to deploy on this machine.[/warn]")
    fail("No server IP given", hint="Run: meridian deploy SERVER_IP", hint_type="user")


def _print_success(server_ip: str, cluster: ClusterConfig, request: DeployRequest, links: HandoffLinks) -> None:
    if is_quiet_mode():
        return
    err_console.print("\n  [ok][bold]Done![/bold][/ok] Your proxy server is live.\n")
    if links.error:
        err_console.print(f"  [warn]Connection page unavailable: {links.error}[/warn]")
    if links.subscription_url:
        print_handoff_links(links.subscription_url, page_url=links.page_url)
    err_console.print()
    err_console.print("  [bold]Next[/bold]")
    err_console.print(f"    [info]meridian test {server_ip}[/info]        verify it from your machine")
    err_console.print("    [info]meridian client add alice[/info]   give someone else their own link")
    err_console.print("    [info]meridian setup[/info]              add relays or more servers")
    if request.domain:
        err_console.print()
        err_console.print("  [bold]Cloudflare[/bold]")
        err_console.print(
            f"    [dim]A record {request.domain} -> {server_ip}; DNS only (grey cloud) while deploying.[/dim]"
        )
        err_console.print("    [dim]Afterwards: Proxied (orange), SSL/TLS Full (Strict), WebSockets on.[/dim]")
    err_console.print()
    line()
    err_console.print(f"\n  [dim]Redeploy with the same settings:  {_redeploy_command(server_ip, request)}[/dim]")
    if cluster.panel.url:
        err_console.print(f"  [dim]Remnawave panel (advanced):        {cluster.panel.display_url}[/dim]")
    err_console.print()


def _redeploy_command(server_ip: str, request: DeployRequest) -> str:
    parts = ["meridian deploy", shlex.quote(server_ip)]
    if request.user != "root":
        parts.append(f"--user {shlex.quote(request.user)}")
    if request.ssh_port != 22:
        parts.append(f"--ssh-port {request.ssh_port}")
    if request.sni and request.sni != DEFAULT_SNI:
        parts.append(f"--sni {shlex.quote(request.sni)}")
    if request.domain:
        parts.append(f"--domain {shlex.quote(request.domain)}")
    if request.hysteria2:
        parts.append("--hysteria2")
    if request.client_name and request.client_name != DEFAULT_CLIENT:
        parts.append(f"--client-name {shlex.quote(request.client_name)}")
    if not request.harden:
        parts.append("--no-harden")
    if request.warp:
        parts.append("--warp")
    if not request.geo_block:
        parts.append("--no-geo-block")
    if request.server_name:
        parts.append(f"--display-name {shlex.quote(request.server_name)}")
    if request.icon:
        parts.append(f"--icon {shlex.quote(request.icon)}")
    if request.color:
        parts.append(f"--color {shlex.quote(request.color)}")
    parts.append("--yes")
    return " ".join(parts)


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


def _load_request(source: str) -> DeployRequest:
    try:
        text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"Could not read deploy request: {exc}", hint_type="user")
    try:
        return DeployRequest.model_validate_json(text)
    except ValidationError as exc:
        error = wrap_validation_error("Invalid deploy request JSON", exc)
        fail(str(error), hint=error.hint, hint_type="user")


def _build_request(**fields: Any) -> DeployRequest:
    try:
        return DeployRequest(**fields)
    except ValidationError as exc:
        error = wrap_validation_error("Invalid deploy request", exc)
        fail(str(error), hint=error.hint, hint_type="user")


def _emit_json(result: DeployResult | DeployPlan, *, operation: OperationContext, status: OutputStatus) -> None:
    summary = (
        result.summary if isinstance(result, DeployResult) else f"Deploy plan: {result.mode} for {result.server_ip}"
    )
    emit_json(
        command_envelope(
            command="deploy",
            data=result.model_dump(mode="json"),
            summary=Summary(
                text=summary,
                changed=status == "changed",
                counts={"nodes": result.node_count, "relays": result.relay_count},
            ),
            status=status,
            exit_code=0,
            timer=operation.timer,
        )
    )


def _emit_completed(
    reporter: Reporter, operation: OperationContext, *, mode: str, server_ip: str, dry_run: bool
) -> None:
    emit_event(
        reporter,
        operation,
        COMMAND_COMPLETED,
        phase="deploy",
        message="Deploy dry-run completed" if dry_run else "Deploy completed",
        data={"command": "deploy", "dry_run": dry_run, "mode": mode, "server_ip": server_ip},
    )
