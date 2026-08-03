"""External active-probe workflow and CLI presentation."""

from __future__ import annotations

from dataclasses import replace

import typer
from rich.markup import escape

from meridian.cluster import ClusterConfig
from meridian.config import SERVER_PROFILES_FILE
from meridian.console import err_console, error_context, fail, is_json_mode
from meridian.core.errors import MeridianError as MeridianException
from meridian.core.inputs import validate_hostname_value
from meridian.core.models import MeridianError, OutputStatus, Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.redaction import redact_string
from meridian.core.verification import (
    ProbeResult,
    VerificationTarget,
)
from meridian.diagnostics.probe import run_probe_checks
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
    requested_server: str = "",
    sni: str = "",
    timeout: float | str = 5,
) -> None:
    """Inspect a public target from the same perspective as an active prober."""
    operation = OperationContext()
    with error_context("probe", timer=operation.timer):
        try:
            result = collect_probe_result(
                target_value,
                requested_server,
                sni=sni,
                timeout=timeout,
            )
        except MeridianException as exc:
            fail(exc)
        except OSError as exc:
            fail(
                f"Probe could not access local state or the network: {exc}",
                hint="Check local file permissions and network access, then retry.",
                hint_type="system",
            )
        except Exception as exc:  # Command boundary: unexpected errors are bugs.
            fail(f"Probe failed unexpectedly: {exc}", hint_type="bug")

    if is_json_mode():
        _emit_json_result(result, operation=operation)
    else:
        _render_result(result)
    if result.exit_code:
        raise typer.Exit(result.exit_code)


def collect_probe_result(
    target_value: str,
    requested_server: str,
    *,
    sni: str = "",
    timeout: float | str = 5,
    registry: ServerRegistry | None = None,
    cluster: ClusterConfig | None = None,
) -> ProbeResult:
    """Run probe checks and return a reusable typed result."""
    timeout = normalize_verification_timeout(timeout)
    registry = registry or ServerRegistry(SERVER_PROFILES_FILE)
    if sni:
        try:
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
    _cluster, deployment, context_check = resolve_deployment_verification_context(
        registry,
        target.ip,
        cluster=cluster,
        allow_external_fallback=bool(target_value and not target.local),
    )
    if target.domain and target.domain not in deployment.domains:
        port = deployment.public_tls_ports[0] if deployment.public_tls_ports else 443
        deployment = replace(
            deployment,
            domains=(*deployment.domains, target.domain),
            domain_ports={**deployment.domain_ports, target.domain: port},
            https_routes=(
                *deployment.https_routes,
                VerificationHttpsRoute(
                    kind="generic" if deployment.kind == "external" else "managed",
                    port=port,
                    tls_sni=target.domain,
                    host_header=target.domain,
                ),
            ),
            endpoint_addresses=(*deployment.endpoint_addresses, target.domain),
        )
    checks = run_probe_checks(
        target.ip,
        deployment,
        requested_sni=sni,
        timeout=timeout,
    )
    if context_check is not None:
        checks.insert(0, context_check)
    return ProbeResult.from_checks(
        target=VerificationTarget(
            requested=target_value or requested_server or target.label,
            resolved_ip=target.ip,
            server_ref=requested_server,
        ),
        context=core_verification_context(deployment, domain=target.domain, sni=sni),
        checks=checks,
    )


def _summary_text(result: ProbeResult) -> str:
    if result.verdict == "passed":
        return f"All {result.counts.checks} probe checks passed."
    if result.verdict == "findings":
        return f"Probe completed with {result.counts.failed} failed and {result.counts.warnings} warning check(s)."
    return f"Probe was inconclusive: {result.counts.skipped} check(s) could not complete."


def _emit_json_result(result: ProbeResult, *, operation: OperationContext) -> None:
    summary = _summary_text(result)
    errors = None
    status: OutputStatus = "ok"
    if result.verdict == "inconclusive":
        status = "failed"
        errors = [
            MeridianError(
                code="MERIDIAN_PROBE_INCONCLUSIVE",
                category="system",
                message=summary,
                hint="Retry from a network that can reach every required target.",
                retryable=True,
                exit_code=3,
            )
        ]
    emit_json(
        command_envelope(
            command="probe",
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


def _render_result(result: ProbeResult) -> None:
    target = result.target.requested
    if result.target.resolved_ip and result.target.resolved_ip not in target:
        target = f"{target} ({result.target.resolved_ip})"
    err_console.print()
    err_console.print("  [bold]Censor probe[/bold]")
    mode = "Meridian deployment policy" if result.context.mode == "meridian" else "generic observations"
    err_console.print(f"  [dim]{escape(redact_string(target))} - {mode}[/dim]")
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
