"""Typed verification results shared by probe, test, CLI, and Engine adapters."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from meridian.core.models import CoreModel
from meridian.core.topology import ProtocolKind, ServerCapability

VerificationStatus = Literal["passed", "failed", "warning", "skipped"]
VerificationVerdict = Literal["passed", "findings", "inconclusive"]
VerificationCompletedExitCode = Literal[0, 3, 4]
VerificationMode = Literal["generic", "meridian"]


class VerificationFinding(CoreModel):
    """One structured observation produced by a verification check."""

    code: str
    status: VerificationStatus
    message: str
    remediation: str = ""


class VerificationCheck(CoreModel):
    """One independently reportable verification check."""

    id: str
    name: str
    status: VerificationStatus
    findings: list[VerificationFinding] = Field(default_factory=list)
    duration_ms: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        statuses = {finding.status for finding in self.findings}
        if "failed" in statuses:
            expected: VerificationStatus = "failed"
        elif "warning" in statuses:
            expected = "warning"
        elif "skipped" in statuses or not statuses:
            expected = "skipped"
        else:
            expected = "passed"
        if self.status != expected:
            raise ValueError(f"Verification check status must be {expected!r} for its findings.")
        return self


class VerificationCounts(CoreModel):
    """Aggregate check counts by terminal status."""

    checks: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    warnings: int = Field(ge=0)
    skipped: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_total(self) -> Self:
        if self.checks != self.passed + self.failed + self.warnings + self.skipped:
            raise ValueError("Verification check counts must add up to the total.")
        return self


class VerificationAggregate(CoreModel):
    """Purely derived verification verdict, counts, and completed exit code."""

    verdict: VerificationVerdict
    exit_code: VerificationCompletedExitCode
    counts: VerificationCounts


def aggregate_verification(checks: Sequence[VerificationCheck]) -> VerificationAggregate:
    """Derive a fail-closed aggregate from completed verification checks."""
    passed = sum(1 for check in checks if check.status == "passed")
    failed = sum(1 for check in checks if check.status == "failed")
    warnings = sum(1 for check in checks if check.status == "warning")
    skipped = sum(1 for check in checks if check.status == "skipped")
    counts = VerificationCounts(
        checks=len(checks),
        passed=passed,
        failed=failed,
        warnings=warnings,
        skipped=skipped,
    )
    if failed or warnings:
        return VerificationAggregate(verdict="findings", exit_code=4, counts=counts)
    if skipped or not checks:
        return VerificationAggregate(verdict="inconclusive", exit_code=3, counts=counts)
    return VerificationAggregate(verdict="passed", exit_code=0, counts=counts)


def build_verification_check(
    check_id: str,
    name: str,
    findings: Sequence[VerificationFinding],
    *,
    duration_ms: int = 0,
) -> VerificationCheck:
    """Build a check whose status is the fail-closed aggregate of its findings."""
    finding_list = list(findings)
    statuses = {finding.status for finding in finding_list}
    if "failed" in statuses:
        status: VerificationStatus = "failed"
    elif "warning" in statuses:
        status = "warning"
    elif "skipped" in statuses or not finding_list:
        status = "skipped"
    else:
        status = "passed"
    return VerificationCheck(
        id=check_id,
        name=name,
        status=status,
        findings=finding_list,
        duration_ms=duration_ms,
    )


class VerificationTarget(CoreModel):
    """Requested and resolved identity of one verification target."""

    requested: str
    resolved_ip: str = ""
    server_ref: str = ""


class VerificationContext(CoreModel):
    """Secret-free Meridian metadata available while verifying a target."""

    mode: VerificationMode = "generic"
    roles: list[ServerCapability] = Field(default_factory=list)
    protocols: list[ProtocolKind] = Field(default_factory=list)
    domain: str = ""
    sni: str = ""


class VerificationResult(CoreModel):
    """Shared aggregate result fields for probe and connection verification."""

    target: VerificationTarget
    context: VerificationContext = Field(default_factory=VerificationContext)
    verdict: VerificationVerdict
    exit_code: VerificationCompletedExitCode
    counts: VerificationCounts
    checks: list[VerificationCheck] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_aggregate(self) -> Self:
        check_ids = [check.id for check in self.checks]
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("Verification check IDs must be unique within one result.")
        aggregate = aggregate_verification(self.checks)
        if (self.verdict, self.exit_code, self.counts) != (
            aggregate.verdict,
            aggregate.exit_code,
            aggregate.counts,
        ):
            raise ValueError("Verification verdict, exit code, and counts must match the check results.")
        return self

    @classmethod
    def from_checks(
        cls,
        *,
        target: VerificationTarget,
        checks: Sequence[VerificationCheck],
        context: VerificationContext | None = None,
    ) -> Self:
        """Build a consistent result from target metadata and completed checks."""
        check_list = list(checks)
        aggregate = aggregate_verification(check_list)
        return cls(
            target=target,
            context=context or VerificationContext(),
            verdict=aggregate.verdict,
            exit_code=aggregate.exit_code,
            counts=aggregate.counts,
            checks=check_list,
        )

    def to_data(self) -> dict[str, Any]:
        from meridian.core.serde import to_plain

        return to_plain(self)


class ProbeResult(VerificationResult):
    """External fingerprint and exposure verification result."""


class TestResult(VerificationResult):
    """End-to-end proxy connection verification result."""

    scope: Literal["basic", "full"] = "full"
    client: str = ""
