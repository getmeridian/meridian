"""External active-probe workflow and CLI presentation."""

from __future__ import annotations

from dataclasses import replace

import typer

from meridian.cluster import ClusterConfig
from meridian.commands.check_render import emit_verification_json, render_verification
from meridian.config import SERVER_PROFILES_FILE
from meridian.console import error_context, fail, is_json_mode
from meridian.core.errors import MeridianError
from meridian.core.inputs import validate_hostname_value
from meridian.core.output import OperationContext
from meridian.core.verification import (
    ProbeResult,
    VerificationTarget,
)
from meridian.diagnostics.probe import run_probe_checks
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
        except MeridianError as exc:
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
        local_vantage=target.local,
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
    emit_verification_json(
        command="probe",
        data=result.to_data(),
        verdict=result.verdict,
        counts=result.counts,
        exit_code=result.exit_code,
        summary=_summary_text(result),
        timer=operation.timer,
        inconclusive_code="MERIDIAN_PROBE_INCONCLUSIVE",
        inconclusive_hint="Retry from a network that can reach every required target.",
    )


def _render_result(result: ProbeResult) -> None:
    target = result.target.requested
    if result.target.resolved_ip and result.target.resolved_ip not in target:
        target = f"{target} ({result.target.resolved_ip})"
    mode = "Meridian deployment policy" if result.context.mode == "meridian" else "generic observations"
    render_verification(
        "Censor probe", f"{target} - {mode}", result.checks, result.verdict, _summary_text(result), redact=True
    )
