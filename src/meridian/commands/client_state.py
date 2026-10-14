"""Local-state handling for client commands after remote mutations."""

from __future__ import annotations

from meridian.cluster import ClusterConfig
from meridian.commands._helpers import LOCAL_STATE_SAVE_ERRORS
from meridian.console import fail
from meridian.core.models import ErrorModel
from meridian.core.redaction import redact_string


def persist_client_state(cluster: ClusterConfig) -> str:
    """Persist client state, returning a redacted failure instead of losing remote-mutation evidence."""
    try:
        cluster.save()
    except LOCAL_STATE_SAVE_ERRORS as exc:
        return _failure_text(exc)
    return ""


def build_client_persistence_warning(
    *,
    operation: str,
    reason: str,
    client: str = "",
    remote_state_changed: bool = True,
) -> ErrorModel:
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
    return ErrorModel(
        code="MERIDIAN_CLIENT_LOCAL_STATE_SAVE_FAILED",
        category="system",
        message=message,
        hint=hint,
        retryable=True,
        exit_code=3,
        details=details,
    )


def require_access_user(cluster: ClusterConfig, name: str) -> None:
    """Require client mutations to target a user declared in the topology."""
    intent = cluster.topology_intent
    if intent is None or name not in intent.access.users:
        fail(
            f"Client '{name}' is not one of your fleet's clients",
            hint="Only clients added with `meridian client add` can be changed by this command.",
            hint_type="user",
        )


def _failure_text(exc: Exception) -> str:
    return redact_string(str(exc)) or type(exc).__name__
