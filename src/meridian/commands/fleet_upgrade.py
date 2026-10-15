"""`meridian fleet upgrade` -- move Remnawave and Realm to newer same-major releases through the reviewed apply."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING

import typer
from rich.markup import escape

from meridian.binary_pins import UPGRADABLE_BINARIES, BinaryPin, effective_binary_pins
from meridian.commands._helpers import apply_reviewed_intent, load_topology_cluster, require_intent, reviewed_runtime
from meridian.config import HOME_DISPLAY
from meridian.console import confirm, err_console, fail, info, ok, warn
from meridian.core.errors import LocalStateError, MeridianError
from meridian.diagnostics.images import (
    ImageComponent,
    ImageObservation,
    ServerImages,
    control_components,
    node_components,
    observe_images,
)
from meridian.host_runtime import PANEL_DATABASE
from meridian.remnawave.versions import UPGRADABLE_PINS, ImageStatus, data_major, effective_pins, parse_image

if TYPE_CHECKING:
    from meridian.cluster import ClusterConfig
    from meridian.core.topology import SetupIntent
    from meridian.servers import ServerRegistry

_BINARY_LABELS = {"realm": "Realm"}

_STATUS_TEXT: dict[ImageStatus, str] = {
    "current": "[green]current[/green]",
    "upgrade": "[yellow]upgrade[/yellow]",
    "absent": "[yellow]install[/yellow]",
    "kept_newer": "[cyan]kept: newer[/cyan]",
    "kept_unmanaged": "[cyan]kept: operator tag[/cyan]",
    "held_major": "[red]held: major[/red]",
}


def run_upgrade(*, yes: bool = False, tested: bool = False) -> None:
    """Resolve target images, compare them with what runs, apply the reviewed plan, then check every server."""
    from meridian.config import SERVER_PROFILES_FILE
    from meridian.servers import ServerRegistry

    cluster = load_topology_cluster()
    intent = require_intent(cluster)
    registry = ServerRegistry(SERVER_PROFILES_FILE)

    targets = _target_pins(cluster, tested=tested)
    observed = _observe(registry, intent, targets)
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
    binaries = _binary_targets(cluster, intent, tested=tested)
    relays = _observe_relays(registry, intent) if binaries else []
    realm_target = binaries["realm"][1].version if binaries else ""
    _render_binaries(relays, realm_target)
    if any(not relay.available for relay in relays):
        for relay in relays:
            if not relay.available:
                warn(f"{relay.label}: could not read its Realm version ({relay.error})")
        fail(
            "Cannot tell what an upgrade would change on every relay",
            hint="Restore SSH access (check with `meridian check SERVER`), then rerun `meridian fleet upgrade`.",
            hint_type="system",
        )
    overrides = {name: image for name, image in targets.items() if image != UPGRADABLE_PINS[name]}
    binary_overrides = {name: target for name, (_, target) in binaries.items() if target != UPGRADABLE_BINARIES[name]}
    binary_changes = sum(1 for relay in relays if relay.running != realm_target)
    changes = sum(len(server.changes) for server in observed) + binary_changes
    if not changes:
        if overrides != cluster.image_pins or binary_overrides != cluster.binary_pins:
            _save_pins(cluster, overrides, binary_overrides)
        ok("Every server already runs its target images (operator-chosen tags are kept)")
        return
    for name in overrides:
        warn(f"{_short(targets[name])} is newer than this Meridian release was tested with")
    for name, pin in binary_overrides.items():
        warn(f"{_BINARY_LABELS[name]} {pin.version} is newer than this Meridian release was tested with")
    if not yes and not confirm(f"Apply {changes} change(s) through the reviewed plan?"):
        raise typer.Exit(1)

    _save_pins(cluster, overrides, binary_overrides)
    _apply(cluster, registry, intent)
    after = _observe(registry, intent, targets)
    relays_after = _observe_relays(registry, intent) if binaries else []
    behind = [(server, image) for server in after for image in server.changes]
    behind_relays = [relay for relay in relays_after if relay.running != realm_target]
    unreadable = any(not server.available for server in after) or any(not relay.available for relay in relays_after)
    if behind or behind_relays or unreadable:
        _render(after)
        _render_binaries(relays_after, realm_target)
        fail(
            "The apply finished, but some servers still do not run their target images",
            hint="Inspect the containers with `meridian check`, then rerun `meridian fleet upgrade`.",
            hint_type="system",
        )
    ok(f"Upgraded {changes} component(s); running the health checks")
    from meridian.commands.health import run as run_check

    run_check()


def _target_pins(cluster: ClusterConfig, *, tested: bool) -> dict[str, str]:
    """The newest release in each pin's major, or this CLI's tested pins with *tested*."""
    if tested:
        return effective_pins({})
    from meridian.remnawave.registry import RegistryError, latest_release

    info("Checking Docker Hub for newer Remnawave releases in the tested major...")
    targets: dict[str, str] = {}
    for name, image in effective_pins(cluster.image_pins).items():
        try:
            targets[name] = latest_release(image)
        except RegistryError as exc:
            warn(f"{exc}; keeping {_short(image)}")
            targets[name] = image
    return targets


def _binary_targets(
    cluster: ClusterConfig, intent: SetupIntent, *, tested: bool
) -> dict[str, tuple[BinaryPin, BinaryPin]]:
    """``(pinned, target)`` for each server binary the fleet runs; *tested* asks no one and moves nothing.

    Realm runs only on relays, so a fleet without relay hops has nothing to upgrade.
    """
    from meridian.compiler import compile_topology
    from meridian.compiler.models import RealmHopPayload

    if not any(isinstance(resource.payload, RealmHopPayload) for resource in compile_topology(intent).resources):
        return {}
    pinned = effective_binary_pins(cluster.binary_pins)
    if tested:
        return {name: (pin, pin) for name, pin in pinned.items()}
    from meridian.github_releases import ReleaseError, latest_binary_release

    info("Checking GitHub for newer Realm releases in the tested major...")
    targets: dict[str, tuple[BinaryPin, BinaryPin]] = {}
    for name, pin in pinned.items():
        try:
            targets[name] = (pin, latest_binary_release(name, pin))
        except ReleaseError as exc:
            warn(f"{exc}; keeping {_BINARY_LABELS[name]} {pin.version}")
            if exc.hint:
                info(exc.hint)
            targets[name] = (pin, pin)
    return targets


@dataclass(frozen=True)
class RelayRealm:
    """The Realm version one relay runs; ``""`` when it is missing or does not start, ``error`` when unreadable."""

    server_ref: str
    label: str
    running: str = ""
    error: str = ""

    @property
    def available(self) -> bool:
        return not self.error


def _observe_relays(registry: ServerRegistry, intent: SetupIntent) -> list[RelayRealm]:
    """Read the installed Realm version on every relay, in parallel and read-only.

    The saved pin only says what the next apply installs; a failed upgrade leaves it ahead of the relay.
    """
    from meridian.compiler import compile_topology
    from meridian.compiler.models import RealmHopPayload
    from meridian.config import RELAY_BINARY_PATH
    from meridian.provision.relay import parse_realm_version
    from meridian.resolve import connection_for_entry

    servers = list(
        dict.fromkeys(
            resource.payload.server_ref
            for resource in compile_topology(intent).resources
            if isinstance(resource.payload, RealmHopPayload)
        )
    )

    def observe(server_ref: str) -> RelayRealm:
        try:
            entry = registry.find(server_ref)
        except LocalStateError as exc:
            return RelayRealm(server_ref, server_ref, error=str(exc))
        if entry is None:
            return RelayRealm(server_ref, server_ref, error="the saved server profile is missing")
        label = entry.name or entry.host
        try:
            result = connection_for_entry(entry).run(f"{RELAY_BINARY_PATH} --version 2>/dev/null", timeout=15)
        except Exception as exc:  # noqa: BLE001 - any transport failure is unavailable evidence
            return RelayRealm(server_ref, label, error=str(exc) or type(exc).__name__)
        if result.returncode in (124, 255):
            detail = result.stderr.strip().splitlines()
            return RelayRealm(server_ref, label, error=detail[-1] if detail else f"exit {result.returncode}")
        # Missing (127) or failing to start (a missing glibc symbol) both mean the next apply installs it.
        return RelayRealm(server_ref, label, parse_realm_version(result.stdout) if result.returncode == 0 else "")

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(servers)))) as executor:
        return list(executor.map(observe, servers))


def _save_pins(cluster: ClusterConfig, overrides: dict[str, str], binary_overrides: dict[str, BinaryPin]) -> None:
    """Record the approved releases so every later apply renders them too."""
    cluster.image_pins = overrides
    cluster.binary_pins = binary_overrides
    try:
        cluster.save()
    except OSError as exc:
        fail(f"Could not save the image pins: {exc}", hint=f"Check {HOME_DISPLAY} permissions.", hint_type="system")


def _observe(registry: ServerRegistry, intent: SetupIntent, pins: dict[str, str] | None = None) -> list[ServerImages]:
    """Observe every server that runs Remnawave containers, in parallel and read-only."""
    from meridian.compiler import compile_topology
    from meridian.compiler.models import ControlPlaneRuntimePayload, NodeRuntimePayload
    from meridian.resolve import connection_for_entry

    control, node = control_components(pins), node_components(pins)
    components: dict[str, tuple[ImageComponent, ...]] = {}
    for resource in compile_topology(intent).resources:
        payload = resource.payload
        if isinstance(payload, ControlPlaneRuntimePayload):
            components[payload.server_ref] = control + components.get(payload.server_ref, ())
        elif isinstance(payload, NodeRuntimePayload) and node[0] not in components.get(payload.server_ref, ()):
            components[payload.server_ref] = components.get(payload.server_ref, ()) + node

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
    table.add_column("Target", style="dim")
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


def _render_binaries(relays: list[RelayRealm], target: str) -> None:
    if not relays:
        return
    from rich.box import ROUNDED
    from rich.table import Table

    table = Table(title="Server binaries", box=ROUNDED, pad_edge=False, padding=(0, 1))
    table.add_column("Server", style="bold cyan")
    table.add_column("Component")
    table.add_column("Running", style="dim")
    table.add_column("Target", style="dim")
    table.add_column("Status")
    for relay in relays:
        if not relay.available:
            table.add_row(escape(relay.label), "Realm", "-", "-", "[red]unavailable[/red]")
            continue
        status: ImageStatus = "current" if relay.running == target else "upgrade" if relay.running else "absent"
        table.add_row(escape(relay.label), "Realm", relay.running or "absent", target, _STATUS_TEXT[status])
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
            f"Back up first (`docker exec {PANEL_DATABASE.name} pg_dumpall -U meridian > panel.sql`), "
            "then follow the Remnawave PostgreSQL upgrade guide. Meridian never moves a PostgreSQL major."
        )


def _apply(cluster: ClusterConfig, registry: ServerRegistry, intent: SetupIntent) -> None:
    """Review the unchanged intent with the new pins and apply exactly that review."""
    from meridian.core.setup import SetupDraft

    try:
        review = reviewed_runtime(registry, cluster).review(SetupDraft.from_intent(intent), cluster=cluster)
    except MeridianError as exc:
        fail(exc)
    if review.retiring:
        fail(
            f"The saved topology still has {len(review.retiring)} unapplied removal(s)",
            hint="Review and apply them first with `meridian plan` and `meridian apply`, then rerun the upgrade.",
            hint_type="user",
        )
    apply_reviewed_intent(
        cluster,
        registry,
        intent,
        failure="The fleet upgrade did not converge",
        hint="Fix the cause and rerun `meridian fleet upgrade`.",
        expected_plan_hash=review.review_hash,
    )
