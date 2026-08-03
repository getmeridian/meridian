"""Local-state handling for client commands after remote mutations."""

from __future__ import annotations

from collections.abc import Callable

from meridian.cluster import ClusterConfig, ClusterConfigExternallyModifiedError
from meridian.console import fail
from meridian.core.models import MeridianError
from meridian.core.redaction import redact_string


def capture_client_state_failure(update: Callable[[], None]) -> str:
    """Run a local client-state update and return a redacted failure."""
    try:
        update()
    except (ClusterConfigExternallyModifiedError, OSError, ValueError) as exc:
        return _failure_text(exc)
    return ""


def persist_client_state(cluster: ClusterConfig) -> str:
    """Persist client state, returning a redacted failure instead of losing remote-mutation evidence."""
    try:
        cluster.save()
    except (ClusterConfigExternallyModifiedError, OSError, ValueError) as exc:
        return _failure_text(exc)
    return ""


def build_client_persistence_warning(
    *,
    operation: str,
    reason: str,
    client: str = "",
    remote_state_changed: bool = True,
) -> MeridianError:
    """Build the typed partial warning shared by client mutation commands."""
    target = f" '{client}'" if client else ""
    details: dict[str, object] = {"remote_state_changed": remote_state_changed}
    if client:
        details["client"] = client
    if remote_state_changed:
        message = (
            f"Remote state changed while {operation}{target}, but Meridian could not save the matching local state: "
            f"{reason}"
        )
        hint = (
            "Repair cluster.yml persistence and inspect the current panel and host state before retrying the mutation."
        )
    else:
        message = (
            f"Meridian observed remote state while {operation}{target}, but could not save the matching local "
            f"evidence: {reason}"
        )
        hint = "Repair cluster.yml persistence, then rerun the command to record the observed evidence."
    return MeridianError(
        code="MERIDIAN_CLIENT_LOCAL_STATE_SAVE_FAILED",
        category="system",
        message=message,
        hint=hint,
        retryable=True,
        exit_code=3,
        details=details,
    )


def require_v4_access_user(cluster: ClusterConfig, name: str) -> bool:
    """Require V4 mutations to target a declared access user."""
    intent = cluster.topology_intent
    if intent is None:
        return False
    if name not in intent.access.users:
        fail(
            f"Client '{name}' is not managed by the V4 access intent",
            hint="Only declared V4 access users may be changed by this command.",
            hint_type="user",
        )
    return True


def _failure_text(exc: Exception) -> str:
    return redact_string(str(exc)) or type(exc).__name__
