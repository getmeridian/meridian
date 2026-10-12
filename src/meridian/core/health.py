"""Typed result of `meridian check`: one report per server and scope, on the verification contract."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Literal

from pydantic import Field

from meridian.core.models import CoreModel
from meridian.core.verification import (
    VerificationCheck,
    VerificationCompletedExitCode,
    VerificationCounts,
    VerificationVerdict,
    aggregate_verification,
)

# preflight: can this server run Meridian; server: control/exit/gateway health; relay: Realm hops.
HealthScope = Literal["preflight", "server", "relay"]


class HealthReport(CoreModel):
    """Checks for one server in one scope."""

    target: str
    server_ref: str = ""
    scope: HealthScope
    roles: list[str] = Field(default_factory=list)
    verdict: VerificationVerdict
    exit_code: VerificationCompletedExitCode
    counts: VerificationCounts
    checks: list[VerificationCheck] = Field(default_factory=list)


class HealthCheckResult(CoreModel):
    """Every report `meridian check` produced, with one fail-closed aggregate."""

    reports: list[HealthReport] = Field(default_factory=list)
    verdict: VerificationVerdict
    exit_code: VerificationCompletedExitCode
    counts: VerificationCounts

    def to_data(self) -> dict[str, Any]:
        from meridian.core.serde import to_plain

        return to_plain(self)


def health_report(
    target: str,
    scope: HealthScope,
    checks: Sequence[VerificationCheck],
    *,
    server_ref: str = "",
    roles: Iterable[str] = (),
) -> HealthReport:
    aggregate = aggregate_verification(checks)
    return HealthReport(
        target=target,
        server_ref=server_ref,
        scope=scope,
        roles=list(roles),
        verdict=aggregate.verdict,
        exit_code=aggregate.exit_code,
        counts=aggregate.counts,
        checks=list(checks),
    )


def combine_health_reports(reports: Sequence[HealthReport]) -> HealthCheckResult:
    aggregate = aggregate_verification([check for report in reports for check in report.checks])
    return HealthCheckResult(
        reports=list(reports),
        verdict=aggregate.verdict,
        exit_code=aggregate.exit_code,
        counts=aggregate.counts,
    )
