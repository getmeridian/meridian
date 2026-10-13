"""Cooperative cancellation contracts for long-running operations.

Cancellation is a request, never a kill: workers poll a token only at safe
points (between provisioning steps, between reconciler actions, between deploy
phases). Work already in flight, including a running SSH command, finishes and
records its own outcome before the token is honored.
"""

from __future__ import annotations

from typing import Literal, Protocol

from meridian.core.errors import MeridianError
from meridian.core.models import CoreModel

CancellationPhase = Literal["queued", "preflight", "provision", "apply", "verify", "handoff"]

RESUME_HINT = "Run the same deploy again; finished steps are observed and skipped."


class CancellationToken(Protocol):
    """Read-only view of a cancellation request."""

    def is_cancelled(self) -> bool:
        """Return True once cancellation has been requested."""


class OperationCancellation(CoreModel):
    """Typed terminal record for an operation stopped at a safe point."""

    phase: CancellationPhase
    message: str
    resume_hint: str = RESUME_HINT
    remote_changed: bool = False


class OperationCancelledError(MeridianError):
    """Raised at a safe point after cancellation was requested.

    It is not a failure: every started mutation already recorded its outcome,
    so re-running the same operation resumes from durable checkpoints.
    """

    def __init__(self, cancellation: OperationCancellation) -> None:
        super().__init__(
            cancellation.message,
            hint=cancellation.resume_hint,
            category="cancelled",
            retryable=True,
        )
        self.cancellation = cancellation


def raise_if_cancelled(
    token: CancellationToken | None,
    *,
    phase: CancellationPhase,
    message: str,
    remote_changed: bool = False,
) -> None:
    """Stop at this safe point when cancellation has been requested."""
    if token is not None and token.is_cancelled():
        raise OperationCancelledError(
            OperationCancellation(phase=phase, message=message, remote_changed=remote_changed)
        )
