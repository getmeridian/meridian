"""End-to-end proxy connectivity workflow and CLI presentation."""

from __future__ import annotations

from dataclasses import replace

import typer
from rich.markup import escape

from meridian.cluster import ClusterConfig
from meridian.config import SERVER_PROFILES_FILE
from meridian.connectivity import collect_connectivity_checks
from meridian.console import err_console, error_context, fail, is_json_mode
from meridian.core.errors import MeridianError as MeridianException
from meridian.core.inputs import validate_hostname_value
from meridian.core.models import MeridianError, OutputStatus, Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.redaction import redact_string
from meridian.core.verification import (
    TestResult,
    VerificationTarget,
    aggregate_verification,
)
from meridian.renderers import emit_json
from meridian.servers import ServerRegistry
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
            result = collect_test_result(
                target_value,
                domain,
                sni,
                requested_server,
                client=client,
                basic=basic,
                timeout=timeout,
            )
        except MeridianException as exc:
            fail(exc)
        except OSError as exc:
            fail(
                f"Connection test could not access local state or the network: {exc}",
                hint="Check local file permissions and network access, then retry.",
                hint_type="system",
            )
        except Exception as exc:  # Command boundary: unexpected errors are bugs.
            fail(f"Connection test failed unexpectedly: {exc}", hint_type="bug")

    if is_json_mode():
        _emit_json_result(result, operation=operation)
    else:
        _render_result(result)
    if result.exit_code:
        raise typer.Exit(result.exit_code)


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
    )


def _summary_text(result: TestResult) -> str:
    scope = "Basic reachability" if result.scope == "basic" else "End-to-end connection test"
    if result.verdict == "passed":
        return f"{scope} passed all {result.counts.checks} checks."
    if result.verdict == "findings":
        return f"{scope} completed with {result.counts.failed} failed and {result.counts.warnings} warning check(s)."
    return f"{scope} was inconclusive: {result.counts.skipped} check(s) could not complete."


def _emit_json_result(result: TestResult, *, operation: OperationContext) -> None:
    summary = _summary_text(result)
    errors = None
    status: OutputStatus = "ok"
    if result.verdict == "inconclusive":
        status = "failed"
        errors = [
            MeridianError(
                code="MERIDIAN_TEST_INCONCLUSIVE",
                category="system",
                message=summary,
                hint="Retry after restoring the unavailable dependency or use --basic intentionally.",
                retryable=True,
                exit_code=3,
            )
        ]
    emit_json(
        command_envelope(
            command="test",
            data=result.to_data(),
            summary=Summary(
                text=summary,
                changed=False,
                counts=result.counts.model_dump(),
            ),
            status=status,
            exit_code=result.exit_code,
            errors=errors,
            timer=operation.timer,
        )
    )


def _render_result(result: TestResult) -> None:
    target = result.target.requested
    if result.target.resolved_ip and result.target.resolved_ip not in target:
        target = f"{target} ({result.target.resolved_ip})"
    err_console.print()
    err_console.print("  [bold]Connection test[/bold]")
    scope = "basic reachability" if result.scope == "basic" else "canonical end-to-end traffic"
    client = f" using client {escape(redact_string(result.client))}" if result.client else ""
    err_console.print(f"  [dim]{escape(redact_string(target))} - {scope}{client}[/dim]")
    err_console.print()
    for check in result.checks:
        marker, style = {
            "passed": ("+", "green"),
            "failed": ("x", "red"),
            "warning": ("!", "yellow"),
            "skipped": ("-", "dim"),
        }[check.status]
        err_console.print(f"  [{style}]{marker}[/{style}] [bold]{escape(check.name)}[/bold]")
        for finding in check.findings:
            finding_style = {
                "passed": "dim",
                "failed": "red",
                "warning": "yellow",
                "skipped": "dim",
            }[finding.status]
            message = escape(redact_string(finding.message))
            err_console.print(f"      [{finding_style}]{message}[/{finding_style}]")
            if finding.remediation and finding.status in {"failed", "warning", "skipped"}:
                remediation = escape(redact_string(finding.remediation))
                err_console.print(f"      [dim]Fix: {remediation}[/dim]")
    err_console.print()
    summary = _summary_text(result)
    style = "green" if result.verdict == "passed" else "red" if result.verdict == "findings" else "yellow"
    err_console.print(f"  [{style}][bold]{summary}[/bold][/{style}]")
    err_console.print()
