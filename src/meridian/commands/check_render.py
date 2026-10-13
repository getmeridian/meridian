"""One rendering of verification checks for check, test, and probe."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from rich.markup import escape

from meridian.console import err_console
from meridian.core.models import ErrorModel, OutputStatus, Summary
from meridian.core.output import OperationTimer, command_envelope
from meridian.core.redaction import redact_string
from meridian.core.verification import VerificationCheck, VerificationCounts, VerificationVerdict

_CHECK_MARKER = {
    "passed": ("+", "green"),
    "failed": ("x", "red"),
    "warning": ("!", "yellow"),
    "skipped": ("-", "dim"),
}
_FINDING_STYLE = {"passed": "dim", "failed": "red", "warning": "yellow", "skipped": "dim"}
_VERDICT_STYLE = {"passed": "green", "findings": "red", "inconclusive": "yellow"}


def render_verification(
    title: str,
    subtitle: str,
    checks: Sequence[VerificationCheck],
    verdict: VerificationVerdict,
    summary: str,
    *,
    redact: bool = False,
) -> None:
    """Print a titled check list with one finding per line and the verdict summary."""
    clean = redact_string if redact else (lambda text: text)
    err_console.print()
    err_console.print(f"  [bold]{escape(title)}[/bold]")
    if subtitle:
        err_console.print(f"  [dim]{escape(clean(subtitle))}[/dim]")
    err_console.print()
    for check in checks:
        marker, style = _CHECK_MARKER[check.status]
        err_console.print(f"  [{style}]{marker}[/{style}] [bold]{escape(check.name)}[/bold]")
        for finding in check.findings:
            tone = _FINDING_STYLE[finding.status]
            err_console.print(f"      [{tone}]{escape(clean(finding.message))}[/{tone}]")
            if finding.remediation and finding.status != "passed":
                err_console.print(f"      [dim]Fix: {escape(clean(finding.remediation))}[/dim]")
    err_console.print()
    style = _VERDICT_STYLE[verdict]
    err_console.print(f"  [{style}][bold]{escape(summary)}[/bold][/{style}]")
    err_console.print()


def emit_verification_json(
    *,
    command: str,
    data: dict[str, Any],
    verdict: VerificationVerdict,
    counts: VerificationCounts,
    exit_code: int,
    summary: str,
    timer: OperationTimer,
    inconclusive_code: str,
    inconclusive_hint: str,
) -> None:
    """Emit the verification envelope; an inconclusive result is a retryable system failure."""
    from meridian.renderers import emit_json

    status: OutputStatus = "ok"
    errors = None
    if verdict == "inconclusive":
        status = "failed"
        errors = [
            ErrorModel(
                code=inconclusive_code,
                category="system",
                message=summary,
                hint=inconclusive_hint,
                retryable=True,
                exit_code=3,
            )
        ]
    emit_json(
        command_envelope(
            command=command,
            data=data,
            summary=Summary(text=summary, changed=False, counts=counts.model_dump()),
            status=status,
            exit_code=exit_code,
            errors=errors,
            timer=timer,
        )
    )
