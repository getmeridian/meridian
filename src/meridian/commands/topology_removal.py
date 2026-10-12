"""Shared review, confirmation, and apply for commands that shrink the topology."""

from __future__ import annotations

import typer
from rich.markup import escape

from meridian.cluster import ClusterConfig
from meridian.commands._helpers import persist_reviewed_apply, reviewed_apply_persistence
from meridian.commands.apply_progress import apply_progress
from meridian.console import confirm, err_console, fail, ok
from meridian.core.errors import MeridianError
from meridian.core.reporters import Reporter
from meridian.core.topology import SetupIntent


def apply_topology_removal(
    cluster: ClusterConfig,
    updated: SetupIntent,
    *,
    subject: str,
    yes: bool,
    extra_lines: tuple[str, ...] = (),
) -> None:
    """Show what the edited intent removes, confirm, then apply exactly that review.

    The server stays in the shelf; only its topology role and the resources the
    reviewed plan no longer needs are removed.
    """
    from meridian.config import SERVER_PROFILES_FILE
    from meridian.core.setup import SetupDraft
    from meridian.servers import ServerRegistry
    from meridian.setup.runtime import SetupRuntime

    registry = ServerRegistry(SERVER_PROFILES_FILE)

    def runtime(reporter: Reporter | None = None) -> SetupRuntime:
        return SetupRuntime(registry, cluster_loader=lambda: cluster, persist=persist_reviewed_apply, reporter=reporter)

    try:
        review = runtime().review(SetupDraft.from_intent(updated), cluster=cluster)
    except MeridianError as exc:
        fail(exc)

    err_console.print()
    err_console.print(f"  [bold]Remove {escape(subject)}[/bold]")
    for line in extra_lines:
        err_console.print(f"  {escape(line)}")
    if review.retiring:
        err_console.print(f"  This deletes {len(review.retiring)} managed resources:")
        for binding in review.retiring:
            err_console.print(f"    [red]remove[/red] {escape(binding.logical_id)}")
    else:
        err_console.print("  [dim]Nothing was deployed for it yet; only the topology changes.[/dim]")
    err_console.print(f"  [dim]Review {review.review_hash}[/dim]")
    err_console.print()
    if not yes and not confirm(f"Remove {subject}?"):
        raise typer.Exit(1)

    try:
        with reviewed_apply_persistence(f"Removing {subject}"), apply_progress(registry=registry) as reporter:
            result = runtime(reporter).apply_intent(updated, expected_plan_hash=review.review_hash)
    except MeridianError as exc:
        fail(exc)
    if not result.all_succeeded:
        fail(
            f"{subject[:1].upper()}{subject[1:]} was not fully removed",
            hint=(
                f"{result.failure_summary}. It is already out of the topology; "
                "fix the cause and rerun `meridian apply` to finish the removal."
            ),
            hint_type="system",
        )
    ok(f"Removed {subject} ({len(result.retired)} resources cleaned up). The server stays in your saved servers.")
