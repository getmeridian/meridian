"""End-to-end proxy connectivity workflow and CLI presentation."""

from __future__ import annotations

from dataclasses import replace

import typer
from rich.markup import escape

from meridian.cluster import ClusterConfig
from meridian.commands.check_render import emit_verification_json, render_verification
from meridian.config import SERVER_PROFILES_FILE
from meridian.connectivity import collect_connectivity_checks
from meridian.console import err_console, error_context, fail, is_json_mode
from meridian.core.errors import LocalStateError, MeridianError
from meridian.core.inputs import validate_hostname_value
from meridian.core.output import OperationContext
from meridian.core.verification import (
    FleetTestResult,
    TestResult,
    VerificationTarget,
    aggregate_verification,
)
from meridian.resolve import local_entry
from meridian.servers import ServerEntry, ServerRegistry
from meridian.verification import (
    VerificationHttpsRoute,
    VerificationTargetError,
    core_verification_context,
    normalize_verification_timeout,
    resolve_deployment_verification_context,
    resolve_verification_target,
)


def run(
    target_value: str = "",
    domain: str = "",
    sni: str = "",
    requested_server: str = "",
    client: str = "",
    basic: bool = False,
    timeout: float | str = 5,
) -> None:
    """Verify public reachability and canonical proxy traffic without SSH."""
    operation = OperationContext()
    with error_context("test", timer=operation.timer):
        try:
            fleet = [] if target_value or requested_server or domain or sni else fleet_test_targets()
            result: TestResult | FleetTestResult
            if len(fleet) > 1:
                results = [
                    collect_test_result(
                        "", "", "", entry.name or entry.host, client=client, basic=basic, timeout=timeout
                    )
                    for entry in fleet
                ]
                result = FleetTestResult.combine(results)
            else:
                # A fleet down to one public entry tests it even while other servers stay saved.
                only = fleet[0].name or fleet[0].host if fleet else ""
                result = collect_test_result(
                    target_value,
                    domain,
                    sni,
                    requested_server or only,
                    client=client,
                    basic=basic,
                    timeout=timeout,
                )
        except MeridianError as exc:
            fail(exc)
        except OSError as exc:
            fail(
                f"Connection test could not access local state or the network: {exc}",
                hint="Check local file permissions and network access, then retry.",
                hint_type="system",
            )

    if is_json_mode():
        _emit_json_result(result, operation=operation)
    elif isinstance(result, FleetTestResult):
        for one in result.results:
            _render_result(one)
        err_console.print(f"  [bold]{escape(_summary_text(result))}[/bold]")
        err_console.print()
    else:
        _render_result(result)
    if result.exit_code:
        raise typer.Exit(result.exit_code)


def fleet_test_targets(
    registry: ServerRegistry | None = None,
    cluster: ClusterConfig | None = None,
) -> list[ServerEntry]:
    """Public entry servers a bare `meridian test` covers: every exit, then each relay's first hop.

    Empty when the command runs on a saved server, only one server is saved, or no
    topology is saved; those cases keep single-target resolution. One entry means
    the fleet shrank while other servers stay saved; `run` tests just that one.
    """
    registry = registry or ServerRegistry(SERVER_PROFILES_FILE)
    if local_entry(registry) is not None or len(registry.list()) < 2:
        return []
    try:
        intent = (cluster or ClusterConfig.load()).topology_intent
    except LocalStateError:
        return []
    if intent is None:
        return []
    refs = [exit_.server_ref for exit_ in intent.exits]
    refs += [relay.hop_server_refs[0] for relay in intent.transparent_relays]
    entries = []
    for ref in dict.fromkeys(refs):
        entry = registry.find(ref)
        if entry is None:
            raise VerificationTargetError(
                f"The fleet uses saved server {ref!r}, which is missing.",
                hint="Add it back with `meridian server add`, or test one server with `meridian test NAME`.",
            )
        entries.append(entry)
    return entries


def collect_test_result(
    target_value: str,
    domain: str,
    sni: str,
    requested_server: str,
    *,
    client: str = "",
    basic: bool = False,
    timeout: float | str = 5,
    registry: ServerRegistry | None = None,
    cluster: ClusterConfig | None = None,
    connectivity_collector=collect_connectivity_checks,
) -> TestResult:
    """Run the reusable connectivity workflow and return a typed result."""
    if basic and client:
        raise VerificationTargetError(
            "--client cannot be used with --basic",
            hint="Remove --client or run the full test without --basic.",
        )
    timeout = normalize_verification_timeout(timeout)
    registry = registry or ServerRegistry(SERVER_PROFILES_FILE)
    try:
        if domain:
            domain = validate_hostname_value(domain)
        if sni:
            sni = validate_hostname_value(sni)
    except ValueError as exc:
        raise VerificationTargetError(str(exc)) from exc
    target = resolve_verification_target(
        target_value,
        requested_server,
        registry,
        allow_domain=True,
        timeout=timeout,
    )
    cluster, deployment, context_check = resolve_deployment_verification_context(
        registry,
        target.ip,
        cluster=cluster,
        allow_external_fallback=bool(target_value and not target.local),
    )
    effective_domain = domain or target.domain
    if effective_domain:
        port = deployment.public_tls_ports[0] if deployment.public_tls_ports else 443
        endpoint_addresses = deployment.endpoint_addresses
        if effective_domain not in endpoint_addresses:
            endpoint_addresses = (*endpoint_addresses, effective_domain)
        https_routes = deployment.https_routes
        known_route = any(
            route.host_header == effective_domain and (not sni or route.tls_sni == sni) for route in https_routes
        )
        if not known_route:
            https_routes = (
                *https_routes,
                VerificationHttpsRoute(
                    kind="generic" if deployment.kind == "external" else "managed",
                    port=port,
                    tls_sni=sni or effective_domain,
                    host_header=effective_domain,
                ),
            )
        deployment = replace(
            deployment,
            endpoint_addresses=endpoint_addresses,
            domains=(*deployment.domains, effective_domain),
            domain_ports={**deployment.domain_ports, effective_domain: port},
            https_routes=https_routes,
        )
    run_result = connectivity_collector(
        cluster,
        deployment,
        target.ip,
        domain=effective_domain,
        sni=sni,
        client=client,
        basic=basic,
        timeout=timeout,
    )
    checks = [context_check, *run_result.checks] if context_check is not None else run_result.checks
    aggregate = aggregate_verification(checks)
    return TestResult(
        target=VerificationTarget(
            requested=target_value or requested_server or target.label,
            resolved_ip=target.ip,
            server_ref=requested_server,
        ),
        context=core_verification_context(deployment, domain=effective_domain, sni=sni),
        verdict=aggregate.verdict,
        exit_code=aggregate.exit_code,
        counts=aggregate.counts,
        checks=checks,
        scope="basic" if basic else "full",
        client=run_result.client,
        transports=run_result.transports,
    )


def _summary_text(result: TestResult | FleetTestResult) -> str:
    if isinstance(result, FleetTestResult):
        total = len(result.results)
        if result.verdict == "passed":
            return f"All {total} servers passed."
        failed = sum(1 for one in result.results if one.verdict == "findings")
        unknown = sum(1 for one in result.results if one.verdict == "inconclusive")
        parts = [f"{failed} of {total} servers had failed checks"] if failed else []
        parts += [f"{unknown} of {total} could not be fully tested"] if unknown else []
        return "; ".join(parts) + "."
    scope = "Basic reachability" if result.scope == "basic" else "End-to-end connection test"
    if result.verdict == "passed":
        return f"{scope} passed all {result.counts.checks} checks."
    if result.verdict == "findings":
        return f"{scope} completed with {result.counts.failed} failed and {result.counts.warnings} warning check(s)."
    return f"{scope} was inconclusive: {result.counts.skipped} check(s) could not complete."


def _emit_json_result(result: TestResult | FleetTestResult, *, operation: OperationContext) -> None:
    emit_verification_json(
        command="test",
        data=result.to_data(),
        verdict=result.verdict,
        counts=result.counts,
        exit_code=result.exit_code,
        summary=_summary_text(result),
        timer=operation.timer,
        inconclusive_code="MERIDIAN_TEST_INCONCLUSIVE",
        inconclusive_hint="Retry after restoring the unavailable dependency or use --basic intentionally.",
    )


def _render_result(result: TestResult) -> None:
    target = result.target.requested
    if result.target.resolved_ip and result.target.resolved_ip not in target:
        target = f"{target} ({result.target.resolved_ip})"
    scope = "basic reachability" if result.scope == "basic" else "canonical end-to-end traffic"
    client = f" using client {result.client}" if result.client else ""
    render_verification(
        "Connection test",
        f"{target} - {scope}{client}",
        result.checks,
        result.verdict,
        _summary_text(result),
        redact=True,
    )
