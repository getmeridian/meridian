"""Typed observations for public surfaces that need no socket I/O."""

from __future__ import annotations

from meridian.core.verification import VerificationCheck, VerificationFinding, build_verification_check


def check_udp_surface(ports: tuple[int, ...]) -> VerificationCheck:
    """Report that UDP listeners require a protocol-aware end-to-end test."""
    rendered = ", ".join(f"UDP/{port}" for port in sorted(set(ports)))
    return build_verification_check(
        "udp_surface",
        "UDP surface",
        [
            VerificationFinding(
                code="UDP_PROTOCOL_PROBE_REQUIRED",
                status="skipped",
                message=f"Configured {rendered} cannot be classified by a generic reachability probe",
                remediation="Run `meridian test` with an active client to verify the delivered UDP protocol.",
            )
        ],
    )


def check_no_public_surface() -> VerificationCheck:
    """Prevent a private/internal topology member from receiving a green probe."""
    return build_verification_check(
        "public_surface",
        "Public surface",
        [
            VerificationFinding(
                code="NO_ADVERTISED_LISTENER",
                status="skipped",
                message="The selected topology member has no advertised public listener",
            )
        ],
    )
