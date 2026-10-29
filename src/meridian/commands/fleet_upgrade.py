"""`meridian fleet upgrade` -- move Realm, the control service, and node agents forward through the reviewed apply."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import typer
from rich.markup import escape

from meridian import __version__, config
from meridian.binary_pins import UPGRADABLE_BINARIES, BinaryPin, effective_binary_pins
from meridian.commands._helpers import apply_reviewed_intent, load_topology_cluster, require_intent, reviewed_runtime
from meridian.console import confirm, err_console, fail, info, ok, warn
from meridian.core.errors import LocalStateError, MeridianError
from meridian.core.version_skew import newer_service_hint, service_skew

if TYPE_CHECKING:
    from meridian.cluster import ClusterConfig
    from meridian.core.topology import SetupIntent
    from meridian.servers import ServerRegistry

_BINARY_LABELS = {"realm": "Realm"}

ComponentStatus = Literal["current", "upgrade", "absent", "newer"]
_STATUS_TEXT: dict[ComponentStatus, str] = {
    "current": "[green]current[/green]",
    "upgrade": "[yellow]upgrade[/yellow]",
    "absent": "[yellow]install[/yellow]",
    "newer": "[red]newer than this Meridian[/red]",
}


@dataclass(frozen=True)
class Component:
    """One versioned piece of the fleet: what runs, what the next apply installs, or why it is unreadable."""

    where: str
    name: str
    running: str = ""
    target: str = ""
    status: ComponentStatus = "current"
    error: str = ""

    @property
    def available(self) -> bool:
        return not self.error

    @property
    def behind(self) -> bool:
        return self.available and self.status in ("upgrade", "absent")


def run_upgrade(*, yes: bool = False, tested: bool = False) -> None:
    """Resolve targets, compare them with what runs, apply the reviewed plan, then check every server."""
    from meridian.servers import ServerRegistry

    cluster = load_topology_cluster()
    intent = require_intent(cluster)
    registry = ServerRegistry(config.SERVER_PROFILES_FILE)

    binaries = _binary_targets(cluster, intent, tested=tested)
    realm_target = binaries["realm"][1].version if binaries else ""
    observed = _observe(cluster, registry, intent, realm_target)
    _render(observed)
    unavailable = [component for component in observed if not component.available]
    if unavailable:
        for component in unavailable:
            warn(f"{component.where}: could not read {component.name} ({component.error})")
        fail(
            "Cannot tell what an upgrade would change on every server",
            hint="Restore SSH and service access (check with `meridian check`), then rerun `meridian fleet upgrade`.",
            hint_type="system",
        )
    if any(component.status == "newer" for component in observed):
        newest = max((c.running for c in observed if c.status == "newer" and c.name == "Control service"), default="")
        fail(
            "Part of this fleet runs a newer Meridian than this one",
            hint=newer_service_hint(newest) if newest else "Update this Meridian (meridian update), then retry.",
            hint_type="user",
        )
    binary_overrides = {name: target for name, (_, target) in binaries.items() if target != UPGRADABLE_BINARIES[name]}
    changes = sum(1 for component in observed if component.behind)
    if not changes:
        if binary_overrides != cluster.binary_pins:
            _save_pins(cluster, binary_overrides)
        ok("Every server already runs its target versions")
        return
    for name, pin in binary_overrides.items():
        warn(f"{_BINARY_LABELS[name]} {pin.version} is newer than this Meridian release was tested with")
    if not yes and not confirm(f"Apply {changes} change(s) through the reviewed plan?"):
        raise typer.Exit(1)

    _save_pins(cluster, binary_overrides)
    _apply(cluster, registry, intent)
    after = _observe(cluster, registry, intent, realm_target)
    if any(component.behind or not component.available for component in after):
        _render(after)
        fail(
            "The apply finished, but some servers still do not run their target versions",
            hint="Inspect them with `meridian check`, then rerun `meridian fleet upgrade`.",
            hint_type="system",
        )
    ok(f"Upgraded {changes} component(s); running the health checks")
    from meridian.commands.health import run as run_check

    run_check()


def _observe(
    cluster: ClusterConfig, registry: ServerRegistry, intent: SetupIntent, realm_target: str
) -> list[Component]:
    """The control service, each node's agent, and each relay's Realm, read-only."""
    components = list(_observe_service(cluster, intent))
    if realm_target:
        for relay in _observe_relays(registry, intent):
            components.append(
                Component(
                    relay.label,
                    "Realm",
                    relay.running,
                    realm_target,
                    _status(relay.running, realm_target),
                    relay.error,
                )
            )
    return components


def _observe_service(cluster: ClusterConfig, intent: SetupIntent) -> list[Component]:
    """What the control service and every agent report, against what this Meridian installs."""
    from meridian.control.client import ControlClient, ControlServiceError
    from meridian.control_artifacts import expected_agent_version

    nodes = [exit_.id for exit_ in intent.exits] + [gateway.id for gateway in intent.routing_gateways]
    try:
        with ControlClient(cluster.panel.url, cluster.panel.api_token, timeout=20) as client:
            running = client.health().version
            statuses = {node: client.node(node) for node in nodes}
    except ControlServiceError as exc:
        error = str(exc) or "no answer"
        return [Component("control server", "Control service", error=error)]
    skew = service_skew(__version__, running)
    service_status: ComponentStatus = (
        "newer" if skew == "service_newer" else "upgrade" if skew == "service_older" else "current"
    )
    components = [Component("control server", "Control service", running, __version__, service_status)]
    for node, status in statuses.items():
        reported = (status.agent_version if status is not None else None) or ""
        expected_agent = expected_agent_version(reported)
        if status is None or not expected_agent:
            agent_status: ComponentStatus = "absent" if status is None else "current"
        else:
            agent_status = _status(reported, expected_agent)
        components.append(Component(node, "Node agent", reported, expected_agent, agent_status))
    return components


def _status(running: str, target: str) -> ComponentStatus:
    if not running:
        return "absent"
    return "current" if running == target else "upgrade"


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


def _save_pins(cluster: ClusterConfig, binary_overrides: dict[str, BinaryPin]) -> None:
    """Record the approved releases so every later apply renders them too."""
    cluster.binary_pins = binary_overrides
    try:
        cluster.save()
    except OSError as exc:
        fail(
            f"Could not save the binary pins: {exc}",
            hint=f"Check {config.HOME_DISPLAY} permissions.",
            hint_type="system",
        )


def _render(observed: list[Component]) -> None:
    from rich.box import ROUNDED
    from rich.table import Table

    table = Table(title="Fleet components", box=ROUNDED, pad_edge=False, padding=(0, 1))
    table.add_column("Where", style="bold cyan")
    table.add_column("Component")
    table.add_column("Running", style="dim")
    table.add_column("Target", style="dim")
    table.add_column("Status")
    for component in observed:
        if not component.available:
            table.add_row(escape(component.where), component.name, "-", "-", "[red]unavailable[/red]")
            continue
        table.add_row(
            escape(component.where),
            component.name,
            escape(component.running or "absent"),
            escape(component.target or "-"),
            _STATUS_TEXT[component.status],
        )
    err_console.print()
    err_console.print(table)
    err_console.print()


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
