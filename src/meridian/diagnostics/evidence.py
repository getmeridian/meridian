"""Small constructors shared by typed diagnostic checks."""

from __future__ import annotations

import time

from meridian.core.verification import (
    VerificationCheck,
    VerificationFinding,
    VerificationStatus,
    build_verification_check,
)
from meridian.diagnostics.checks import CheckResult


def finding(
    code: str,
    status: VerificationStatus,
    message: str,
    remediation: str = "",
) -> VerificationFinding:
    return VerificationFinding(
        code=code,
        status=status,
        message=message,
        remediation=remediation,
    )


def finish_check(
    check_id: str,
    name: str,
    findings: list[VerificationFinding],
    started: float,
) -> VerificationCheck:
    return build_verification_check(
        check_id,
        name,
        findings,
        duration_ms=max(0, int((time.monotonic() - started) * 1000)),
    )


def check_from_result(check_id: str, name: str, result: CheckResult) -> VerificationCheck:
    """Lift one SSH health primitive onto the shared verification contract."""
    return build_verification_check(
        check_id, name, [finding(check_id, result.status, result.detail, result.remediation)]
    )
