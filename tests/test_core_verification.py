"""Tests for shared probe and test verification contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from meridian.core.verification import (
    ProbeResult,
    VerificationCheck,
    VerificationContext,
    VerificationCounts,
    VerificationFinding,
    VerificationStatus,
    VerificationTarget,
    aggregate_verification,
    build_verification_check,
)
from meridian.core.verification import (
    TestResult as VerificationTestResult,
)


def _check(status: VerificationStatus) -> VerificationCheck:
    return VerificationCheck(
        id=f"check.{status}",
        name=f"{status.title()} check",
        status=status,
        findings=[
            VerificationFinding(
                code=f"CHECK_{status.upper()}",
                status=status,
                message=f"The check {status}.",
            )
        ],
    )


def test_aggregate_verification_counts_four_states_and_prioritizes_findings() -> None:
    aggregate = aggregate_verification(
        [
            _check("passed"),
            _check("failed"),
            _check("warning"),
            _check("skipped"),
        ]
    )

    assert aggregate.verdict == "findings"
    assert aggregate.exit_code == 4
    assert aggregate.counts == VerificationCounts(
        checks=4,
        passed=1,
        failed=1,
        warnings=1,
        skipped=1,
    )


def test_aggregate_verification_passes_only_when_every_check_passes() -> None:
    aggregate = aggregate_verification((_check("passed"), _check("passed")))

    assert aggregate.verdict == "passed"
    assert aggregate.exit_code == 0
    assert aggregate.counts.passed == 2


def test_aggregate_verification_is_inconclusive_for_skipped_or_empty_checks() -> None:
    skipped = aggregate_verification([_check("skipped")])
    empty = aggregate_verification([])

    assert (skipped.verdict, skipped.exit_code) == ("inconclusive", 3)
    assert skipped.counts.skipped == 1
    assert (empty.verdict, empty.exit_code) == ("inconclusive", 3)
    assert empty.counts.checks == 0


def test_build_verification_check_uses_fail_closed_status_precedence() -> None:
    cases: list[tuple[list[VerificationStatus], VerificationStatus]] = [
        ([], "skipped"),
        (["passed"], "passed"),
        (["passed", "skipped"], "skipped"),
        (["passed", "skipped", "warning"], "warning"),
        (["passed", "warning", "failed"], "failed"),
    ]

    for statuses, expected in cases:
        findings = [
            VerificationFinding(
                code=f"CHECK_{status.upper()}",
                status=status,
                message=f"The check {status}.",
            )
            for status in statuses
        ]
        check = build_verification_check(
            "tls.handshake",
            "TLS handshake",
            findings,
            duration_ms=25,
        )

        assert check.status == expected
        assert check.findings == findings
        assert check.duration_ms == 25


def test_check_contract_rejects_status_that_disagrees_with_findings() -> None:
    with pytest.raises(ValidationError, match="status must be 'failed'"):
        VerificationCheck(
            id="tls",
            name="TLS",
            status="passed",
            findings=[VerificationFinding(code="TLS_FAILED", status="failed", message="failed")],
        )


def test_probe_and_test_results_share_target_context_and_serialization() -> None:
    target = VerificationTarget(
        requested="edge.example",
        resolved_ip="198.51.100.20",
        server_ref="exit-a",
    )
    context = VerificationContext(
        mode="meridian",
        roles=["exit"],
        protocols=["reality", "hysteria2"],
        domain="edge.example",
        sni="www.example.com",
    )
    checks = [_check("passed")]
    aggregate = aggregate_verification(checks)

    probe = ProbeResult.from_checks(target=target, context=context, checks=checks)
    connection_test = VerificationTestResult(
        target=target,
        context=context,
        verdict=aggregate.verdict,
        exit_code=aggregate.exit_code,
        counts=aggregate.counts,
        checks=checks,
        scope="full",
        client="alice",
    )

    assert probe.target == connection_test.target == target
    assert probe.context == connection_test.context == context
    assert probe.to_data() == probe.model_dump(mode="json")
    assert connection_test.verdict == "passed"
    assert connection_test.exit_code == 0
    assert connection_test.to_data()["scope"] == "full"
    assert connection_test.to_data()["client"] == "alice"


def test_verification_result_rejects_an_inconsistent_aggregate() -> None:
    with pytest.raises(ValidationError, match="must match the check results"):
        ProbeResult(
            target=VerificationTarget(requested="198.51.100.20"),
            verdict="passed",
            exit_code=0,
            counts=VerificationCounts(checks=0, passed=0, failed=0, warnings=0, skipped=0),
            checks=[_check("passed")],
        )


def test_verification_result_rejects_duplicate_check_ids() -> None:
    checks = [_check("passed"), _check("passed")]
    aggregate = aggregate_verification(checks)

    with pytest.raises(ValidationError, match="check IDs must be unique"):
        ProbeResult(
            target=VerificationTarget(requested="198.51.100.20"),
            verdict=aggregate.verdict,
            exit_code=aggregate.exit_code,
            counts=aggregate.counts,
            checks=checks,
        )
