"""Small constructors shared by typed diagnostic checks."""

from __future__ import annotations

import time

from meridian.core.verification import (
    VerificationCheck,
    VerificationFinding,
    VerificationStatus,
    build_verification_check,
)


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
