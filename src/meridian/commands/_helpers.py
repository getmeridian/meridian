"""Shared helpers for command modules.

Common patterns used across multiple command files: cluster loading,
panel client creation, and traffic formatting.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import NoReturn

from meridian.cluster import ClusterConfig, ClusterConfigExternallyModifiedError
from meridian.config import HOME_DISPLAY
from meridian.console import fail
from meridian.core.errors import LocalStateError
from meridian.remnawave import MeridianPanel


class ReviewedApplyPersistenceError(Exception):
    """A V4 apply checkpoint could not be written to local state."""


_REVIEWED_APPLY_RECOVERY = (
    "Remote state may have changed. Repair local state, then rerun `meridian plan` and `meridian apply` to reconcile."
)


def has_meridian_v3_state() -> bool:
    """Return True when 3.x left local state in its home (or in an explicit MERIDIAN_HOME)."""
    from meridian import config

    return any(
        (home / "credentials").is_dir() or (home / "servers").is_file()
        for home in {config.LEGACY_HOME, config.MERIDIAN_HOME}
    )


def fail_meridian_v3_state() -> NoReturn:
    """Explain that 3.x deployments are not managed by 4.0, without touching them."""
    fail(
        "Found Meridian 3.x servers, which Meridian 4 cannot manage",
        hint=(
            "Your 3.x servers keep working, and their settings stay in ~/.meridian. "
            "Manage them with 3.x: uvx --from 'meridian-vpn<4' meridian ... "
            "Moving to 4 needs a fresh deploy (`meridian deploy IP` or `meridian setup`), "
            "and every client gets a new link: https://github.com/getmeridian/meridian/releases"
        ),
        hint_type="user",
    )


def load_topology_cluster() -> ClusterConfig:
    """Load a cluster that has a reviewed topology, or explain how to get one."""
    cluster = load_cluster(require_configured=False)
    if cluster.topology_intent is not None:
        return cluster
    if has_meridian_v3_state():
        fail_meridian_v3_state()
    if cluster.is_configured:
        fail(
            "This cluster.yml was written by a Meridian 4.0 development build",
            hint=f"Move {HOME_DISPLAY}/cluster.yml aside, then deploy again to rebuild it from scratch.",
            hint_type="user",
        )
    fail(
        "No fleet yet",
        hint="Deploy one server with `meridian deploy IP`, or several with `meridian setup`.",
        hint_type="user",
    )


def load_cluster(*, require_configured: bool = True) -> ClusterConfig:
    """Load local cluster state through the CLI error boundary."""
    try:
        cluster = ClusterConfig.load()
    except LocalStateError as exc:
        fail(exc)
    if require_configured and not cluster.is_configured:
        if has_meridian_v3_state():
            fail_meridian_v3_state()
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
