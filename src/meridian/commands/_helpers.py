"""Shared helpers for command modules: cluster loading, reviewed apply, panel client, traffic formatting."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, NoReturn

from meridian import config
from meridian.cluster import ClusterConfig, ClusterConfigExternallyModifiedError
from meridian.commands.apply_progress import apply_progress
from meridian.console import fail
from meridian.core.errors import LocalStateError, MeridianError
from meridian.core.topology import ExitIntent, SetupIntent
from meridian.remnawave import MeridianPanel

if TYPE_CHECKING:
    from meridian.core.reporters import Reporter
    from meridian.reconciler.resources import ResourceExecutionResult
    from meridian.servers import ServerRegistry
    from meridian.setup.runtime import SetupRuntime

LOCAL_STATE_SAVE_ERRORS = (ClusterConfigExternallyModifiedError, OSError, ValueError)


class ReviewedApplyPersistenceError(MeridianError):
    """An apply checkpoint could not be written to local state after remote state may have changed."""

    def __init__(self, reason: str) -> None:
        super().__init__(
            f"Could not save local state; remote state may have changed: {reason}",
            hint="Repair local state, then rerun `meridian plan` and `meridian apply` to reconcile.",
            category="system",
            retryable=True,
        )


def has_meridian_v3_state() -> bool:
    """Return True when 3.x left local state in its home (or in an explicit MERIDIAN_HOME)."""
    return any(
        (home / "credentials").is_dir() or (home / "servers").is_file()
        for home in {config.LEGACY_HOME, config.INSTALL_HOME}
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
    cluster = load_cluster()
    if cluster.topology_intent is not None:
        return cluster
    if has_meridian_v3_state():
        fail_meridian_v3_state()
    if cluster.is_configured:
        fail(
            "This cluster.yml was written by a Meridian 4.0 development build",
            hint=f"Move {config.HOME_DISPLAY}/cluster.yml aside, then deploy again to rebuild it from scratch.",
            hint_type="user",
        )
    fail(
        "No fleet yet",
        hint="Deploy one server with `meridian deploy IP`, or several with `meridian setup`.",
        hint_type="user",
    )


def require_intent(cluster: ClusterConfig) -> SetupIntent:
    """Return the intent of a cluster that came from ``load_topology_cluster()``."""
    if cluster.topology_intent is None:
        raise RuntimeError("Topology intent is missing")
    return cluster.topology_intent


def resolve_exit(intent: SetupIntent, query: str) -> ExitIntent:
    """Match an exit by ID, saved server ID, saved name, or address."""
    from meridian.servers import ServerRegistry

    try:
        entry = ServerRegistry(config.SERVER_PROFILES_FILE).find(query)
    except LocalStateError as exc:
        fail(exc)
    server_ref = entry.id if entry is not None else query
    for exit_ in intent.exits:
        if query == exit_.id or server_ref == exit_.server_ref:
            return exit_
    fail(f"Exit {query} not found in the topology", hint="Check: meridian node list", hint_type="user")


def load_cluster() -> ClusterConfig:
    """Load local cluster state through the CLI error boundary."""
    try:
        return ClusterConfig.load()
    except LocalStateError as exc:
        fail(exc)


def make_panel(cluster: ClusterConfig) -> MeridianPanel:
    """Create a MeridianPanel client from cluster config."""
    return MeridianPanel(cluster.panel.url, cluster.panel.api_token)


def persist_reviewed_apply(cluster: ClusterConfig) -> None:
    """Persist an apply checkpoint, marking a failure as one that follows remote changes."""
    try:
        cluster.save()
    except LOCAL_STATE_SAVE_ERRORS as exc:
        raise ReviewedApplyPersistenceError(str(exc)) from exc


def reviewed_runtime(
    registry: ServerRegistry,
    cluster: ClusterConfig,
    reporter: Reporter | None = None,
    *,
    fence_holder: str | None = None,
) -> SetupRuntime:
    """A setup runtime bound to an already-loaded cluster that persists every checkpoint."""
    from meridian.setup.runtime import SetupRuntime

    return SetupRuntime(
        registry,
        cluster_loader=lambda: cluster,
        persist=persist_reviewed_apply,
        reporter=reporter,
        fence_holder=fence_holder,
    )


def apply_reviewed_intent(
    cluster: ClusterConfig,
    registry: ServerRegistry,
    intent: SetupIntent,
    *,
    failure: str,
    hint: str,
    expected_plan_hash: str | None = None,
    on_unsaved: Callable[[ReviewedApplyPersistenceError], NoReturn] | None = None,
    fence_holder: str | None = None,
) -> ResourceExecutionResult:
    """Apply an edited intent with progress, failing with ``failure`` unless every resource converged.

    ``on_unsaved`` lets a command report partial results when remote state changed but local state was not saved;
    ``fence_holder`` continues a lease the caller already holds.
    """
    try:
        with apply_progress(registry=registry) as reporter:
            result = reviewed_runtime(registry, cluster, reporter, fence_holder=fence_holder).apply_intent(
                intent, expected_plan_hash=expected_plan_hash
            )
    except MeridianError as exc:
        if on_unsaved is not None and isinstance(exc, ReviewedApplyPersistenceError):
            on_unsaved(exc)
        fail(exc)
    if not result.all_succeeded:
        fail(failure, hint=f"{result.failure_summary}. {hint}", hint_type="system")
    return result


def ssh_port_answers(host: str, port: int) -> bool:
    """Whether a server's SSH port accepts a TCP connection; one probe, so a blocked server costs 10s."""
    from meridian.health import tcp_connect

    return tcp_connect(host, port, 10)


def forget_unreachable_servers(
    cluster: ClusterConfig,
    registry: ServerRegistry,
    intent: SetupIntent,
    *,
    forget: bool,
    yes: bool,
) -> None:
    """Offer to forget servers that only cleanup still needs and whose SSH port does not answer.

    A blocked or deleted server would fail cleanup on every apply. Forgetting drops
    only its server-side records; the panel still deletes what it holds. ``--yes``
    alone never forgets: an automated run must say ``--forget-unreachable``. Nothing is
    saved here: the caller's reviewed apply persists the forgetting with everything else.
    """
    from rich.markup import escape

    from meridian.compiler.deployment import deployment_server_refs
    from meridian.console import confirm, err_console, is_json_mode
    from meridian.core.setup import SetupDraft
    from meridian.reconciler.retirement import forget_server, retiring_server_refs

    try:
        review = reviewed_runtime(registry, cluster).review(SetupDraft.from_intent(intent), cluster=cluster)
    except MeridianError as exc:
        fail(exc)
    for server_ref in sorted(retiring_server_refs(review.retiring) - deployment_server_refs(intent)):
        entry = registry.find(server_ref)
        if entry is None or entry.runs_here() or ssh_port_answers(entry.host, entry.ssh_port or 22):
            continue
        label = escape(entry.name or entry.host)
        if not forget:
            if yes or is_json_mode():
                if not is_json_mode():
                    err_console.print(
                        f"  [yellow]{label} does not answer over SSH; its cleanup will fail.[/yellow] "
                        "[dim]Pass --forget-unreachable to forget it instead.[/dim]"
                    )
                continue
            err_console.print()
            err_console.print(f"  [bold]{label} does not answer over SSH from here.[/bold]")
            err_console.print("  Meridian can't clean it up, and every apply would keep trying.")
            err_console.print("  Forgetting it skips that cleanup. Whatever runs there keeps serving the users it had,")
            err_console.print("  including clients you remove later, until you delete or wipe it at your provider.")
            if not confirm(f"Forget what Meridian deployed on {entry.name or entry.host}?"):
                continue
        # In memory only: the approved apply saves it, so a declined review forgets nothing.
        forgotten = forget_server(cluster, review.retiring, server_ref)
        err_console.print(
            f"  [dim]This apply forgets {len(forgotten)} resources on {label}; "
            "delete or wipe it at your provider.[/dim]"
        )


def format_traffic(bytes_used: int, bytes_limit: int = 0) -> str:
    """Format traffic usage as a human-readable string."""

    def _human(b: int) -> str:
        if b <= 0:
            return "0 B"
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if abs(b) < 1024:
                # A whole limit reads as typed: "50 GB", not "50.0 GB".
                return f"{b:.1f}".removesuffix(".0") + f" {unit}" if unit != "B" else f"{b} {unit}"
            b /= 1024  # type: ignore[assignment]
        return f"{b:.1f} PB"

    used = _human(bytes_used)
    if bytes_limit > 0:
        return f"{used} / {_human(bytes_limit)}"
    return used
