"""Shared helpers for command modules.

Common patterns used across multiple command files: cluster loading,
panel client creation, and traffic formatting.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from meridian.cluster import ClusterConfig, ClusterConfigExternallyModifiedError
from meridian.console import fail
from meridian.core.errors import LocalStateError
from meridian.remnawave import MeridianPanel


class ReviewedApplyPersistenceError(Exception):
    """A V4 apply checkpoint could not be written to local state."""


_REVIEWED_APPLY_RECOVERY = (
    "Remote state may have changed. Repair local state, then rerun `meridian plan` and `meridian apply` to reconcile."
)


def load_cluster(*, require_configured: bool = True) -> ClusterConfig:
    """Load local cluster state through the CLI error boundary."""
    try:
        cluster = ClusterConfig.load()
    except LocalStateError as exc:
        fail(exc)
    if require_configured and not cluster.is_configured:
        fail(
            "No cluster configured",
            hint="Deploy first: meridian deploy",
            hint_type="user",
        )
    return cluster


def make_panel(cluster: ClusterConfig) -> MeridianPanel:
    """Create a MeridianPanel client from cluster config."""
    return MeridianPanel(cluster.panel.url, cluster.panel.api_token)


@contextmanager
def remote_mutation_persistence(operation: str) -> Iterator[None]:
    """Render local persistence failures after a remote mutation as partial."""
    try:
        yield
    except (ClusterConfigExternallyModifiedError, OSError, ValueError) as exc:
        fail(
            f"{operation}, but Meridian could not save the matching local state",
            hint=(
                f"{exc} Remote state changed. Fix local state permissions or disk space, "
                "review cluster.yml, then rerun the command to reconcile."
            ),
            hint_type="system",
        )


@contextmanager
def reviewed_apply_persistence(operation: str) -> Iterator[None]:
    """Render ambiguous V4 checkpoint write failures at the CLI boundary."""
    try:
        yield
    except ReviewedApplyPersistenceError as exc:
        fail(
            f"{operation} could not save local convergence state",
            hint=str(exc),
            hint_type="system",
        )


def persist_reviewed_apply(cluster: ClusterConfig) -> None:
    """Persist a V4 checkpoint while preserving its failure provenance."""
    try:
        cluster.save()
    except (ClusterConfigExternallyModifiedError, OSError, ValueError) as exc:
        raise ReviewedApplyPersistenceError(f"{exc} {_REVIEWED_APPLY_RECOVERY}") from exc


def format_traffic(bytes_used: int, bytes_limit: int = 0) -> str:
    """Format traffic usage as a human-readable string."""

    def _human(b: int) -> str:
        if b <= 0:
            return "0 B"
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if abs(b) < 1024:
                return f"{b:.1f} {unit}" if unit != "B" else f"{b} {unit}"
            b /= 1024  # type: ignore[assignment]
        return f"{b:.1f} PB"

    used = _human(bytes_used)
    if bytes_limit > 0:
        return f"{used} / {_human(bytes_limit)}"
    return used
