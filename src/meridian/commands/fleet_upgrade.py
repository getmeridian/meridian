"""`meridian fleet upgrade` -- roll this CLI's pinned Remnawave images out through the reviewed apply."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import typer
from rich.markup import escape

from meridian.commands._helpers import load_topology_cluster, persist_reviewed_apply, reviewed_apply_persistence
from meridian.commands.apply_progress import apply_progress
from meridian.console import confirm, err_console, fail, info, ok, warn
from meridian.core.errors import LocalStateError, MeridianError
from meridian.diagnostics.images import (
    CONTROL_COMPONENTS,
    NODE_COMPONENTS,
    ImageComponent,
    ImageObservation,
    ServerImages,
    observe_images,
)
from meridian.remnawave.versions import ImageStatus, data_major, parse_image

if TYPE_CHECKING:
    from meridian.cluster import ClusterConfig
    from meridian.core.topology import SetupIntent
    from meridian.servers import ServerRegistry

_STATUS_TEXT: dict[ImageStatus, str] = {
    "current": "[green]current[/green]",
    "upgrade": "[yellow]upgrade[/yellow]",
    "absent": "[yellow]install[/yellow]",
    "kept_newer": "[cyan]kept: newer[/cyan]",
    "kept_unmanaged": "[cyan]kept: operator tag[/cyan]",
    "held_major": "[red]held: major[/red]",
}


def run_upgrade(*, yes: bool = False) -> None:
    """Compare running images with the pins, apply the reviewed plan, then check every server."""
    from meridian.config import SERVER_PROFILES_FILE
    from meridian.servers import ServerRegistry

    cluster = load_topology_cluster()
    intent = cluster.topology_intent
    if intent is None:
        raise RuntimeError("Topology intent is missing")
    registry = ServerRegistry(SERVER_PROFILES_FILE)

    observed = _observe(registry, intent)
    _render(observed)
    unavailable = [server for server in observed if not server.available]
    if unavailable:
        for server in unavailable:
            warn(f"{server.label}: could not read its images ({server.error})")
        fail(
            "Cannot tell what an upgrade would change on every server",
            hint="Restore SSH access (check with `meridian check SERVER`), then rerun `meridian fleet upgrade`.",
            hint_type="system",
        )
    _explain_held(observed)
    changes = sum(len(server.changes) for server in observed)
    if not changes:
        ok("Every server already runs this CLI's pinned images (operator-chosen tags are kept)")
        return
    if not yes and not confirm(f"Apply {changes} image change(s) through the reviewed plan?"):
        raise typer.Exit(1)

    _apply(cluster, registry, intent)
    after = _observe(registry, intent)
    behind = [(server, image) for server in after for image in server.changes]
    if behind or any(not server.available for server in after):
        _render(after)
        fail(
            "The apply finished, but some servers still do not run their pinned images",
            hint="Inspect the containers with `meridian check`, then rerun `meridian fleet upgrade`.",
            hint_type="system",
        )
    ok(f"Upgraded {changes} image(s); running the health checks")
    from meridian.commands.health import run as run_check

    run_check()


def _observe(registry: ServerRegistry, intent: SetupIntent) -> list[ServerImages]:
    """Observe every server that runs Remnawave containers, in parallel and read-only."""
    from meridian.compiler import compile_topology
    from meridian.compiler.models import ControlPlaneRuntimePayload, NodeRuntimePayload
    from meridian.resolve import connection_for_entry

    components: dict[str, tuple[ImageComponent, ...]] = {}
    for resource in compile_topology(intent).resources:
        payload = resource.payload
        if isinstance(payload, ControlPlaneRuntimePayload):
            components[payload.server_ref] = CONTROL_COMPONENTS + components.get(payload.server_ref, ())
        elif isinstance(payload, NodeRuntimePayload) and NODE_COMPONENTS[0] not in components.get(
            payload.server_ref, ()
        ):
            components[payload.server_ref] = components.get(payload.server_ref, ()) + NODE_COMPONENTS

    def observe(server_ref: str) -> ServerImages:
        try:
            entry = registry.find(server_ref)
        except LocalStateError as exc:
            return ServerImages(server_ref, server_ref, error=str(exc))
        if entry is None:
            return ServerImages(server_ref, server_ref, error="the saved server profile is missing")
        label = entry.name or entry.host
        return observe_images(connection_for_entry(entry), components[server_ref], server_ref=server_ref, label=label)

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(components)))) as executor:
        return list(executor.map(observe, components))


def _render(observed: list[ServerImages]) -> None:
    from rich.box import ROUNDED
    from rich.table import Table

    table = Table(title="Remnawave images", box=ROUNDED, pad_edge=False, padding=(0, 1))
    table.add_column("Server", style="bold cyan")
    table.add_column("Component")
    table.add_column("Running", style="dim")
    table.add_column("Pinned", style="dim")
    table.add_column("Status")
    for server in observed:
        if not server.available:
            table.add_row(escape(server.label), "-", "-", "-", "[red]unavailable[/red]")
            continue
        for image in server.images:
            table.add_row(
                escape(server.label),
                image.component.replace("_", " "),
                escape(_short(image.running) or "absent"),
                escape(_short(image.pinned)),
                _STATUS_TEXT[image.status],
            )
    err_console.print()
    err_console.print(table)
    err_console.print()


def _short(image: str) -> str:
    """The tag plus a 7-character digest; the component column already names the repository."""
    if not image:
        return ""
    ref = parse_image(image)
    digest = ref.digest.rpartition(":")[2][:7]
    label = ref.tag or ref.repository
    return f"{label} @{digest}" if digest else label


def _explain_held(observed: list[ServerImages]) -> None:
    held: list[tuple[ServerImages, ImageObservation]] = [
        (server, image) for server in observed for image in server.images if image.status == "held_major"
    ]
    for server, image in held:
        warn(
            f"{server.label}: PostgreSQL {data_major(image.running)} stays; this CLI pins "
            f"{data_major(image.pinned)}, and a major upgrade needs a dump and restore"
        )
        info(
            "Back up first (`docker exec remnawave-db pg_dumpall -U meridian > remnawave.sql`), "
            "then follow the Remnawave PostgreSQL upgrade guide. Meridian never moves a PostgreSQL major."
        )


def _apply(cluster: ClusterConfig, registry: ServerRegistry, intent: SetupIntent) -> None:
    """Review the unchanged intent with the new pins and apply exactly that review."""
    from meridian.core.reporters import Reporter
    from meridian.core.setup import SetupDraft
    from meridian.setup.runtime import SetupRuntime

    def runtime(reporter: Reporter | None = None) -> SetupRuntime:
        return SetupRuntime(registry, cluster_loader=lambda: cluster, persist=persist_reviewed_apply, reporter=reporter)

    try:
        review = runtime().review(SetupDraft.from_intent(intent), cluster=cluster)
    except MeridianError as exc:
        fail(exc)
    if review.retiring:
        fail(
            f"The saved topology still has {len(review.retiring)} unapplied removal(s)",
            hint="Review and apply them first with `meridian plan` and `meridian apply`, then rerun the upgrade.",
            hint_type="user",
        )
    try:
        with reviewed_apply_persistence("Fleet upgrade"), apply_progress(registry=registry) as reporter:
            result = runtime(reporter).apply_intent(intent, expected_plan_hash=review.review_hash)
    except MeridianError as exc:
        fail(exc)
    if not result.all_succeeded:
        fail(
            "The fleet upgrade did not converge",
            hint=f"{result.failure_summary}. Fix the cause and rerun `meridian fleet upgrade`.",
            hint_type="system",
        )
