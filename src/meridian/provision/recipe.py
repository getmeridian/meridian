"""Provisioning recipe graph primitives.

Recipes make provisioner ordering explicit without turning Meridian into a
generic automation engine. Existing step classes are wrapped as operations so
we can migrate large steps into smaller chunks over time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Iterable

from meridian.provision.steps import Step, StepResult

if TYPE_CHECKING:
    from meridian.ssh import ServerConnection


class Resource(str, Enum):
    """Resources produced and consumed by provisioning operations."""

    DISK_SPACE_CHECKED = "disk_space_checked"
    SYSTEM_PACKAGES = "system_packages"
    AUTO_UPGRADES = "auto_upgrades"
    TIMEZONE_UTC = "timezone_utc"
    SSH_HARDENED = "ssh_hardened"
    FAIL2BAN_RUNNING = "fail2ban_running"
    BBR_ENABLED = "bbr_enabled"
    FIREWALL_CONFIGURED = "firewall_configured"
    HTTPS_ALLOWED = "https_allowed"
    DOCKER_INSTALLED = "docker_installed"
    REMNAWAVE_PANEL_RUNNING = "remnawave_panel_running"
    WARP_CONNECTED = "warp_connected"
    NGINX_INSTALLED = "nginx_installed"
    NGINX_CONFIGURED = "nginx_configured"
    TLS_CERTIFICATE = "tls_certificate"
    PWA_ASSETS = "pwa_assets"
    RELAY_PACKAGES = "relay_packages"
    RELAY_FIREWALL = "relay_firewall"
    REALM_INSTALLED = "realm_installed"
    REALM_CONFIGURED = "realm_configured"
    RELAY_VERIFIED = "relay_verified"

    def __str__(self) -> str:
        return self.value


class RecipeValidationError(ValueError):
    """A recipe cannot be ordered because dependencies are incomplete."""


Condition = Callable[[Any], bool]


def _always(_ctx: Any) -> bool:
    return True


@dataclass
class Operation:
    """A runnable provisioner operation with explicit resource contracts."""

    step: Step
    requires: frozenset[Resource] = field(default_factory=frozenset)
    provides: frozenset[Resource] = field(default_factory=frozenset)
    when: Condition = _always

    @property
    def name(self) -> str:
        return self.step.name

    def active(self, ctx: Any) -> bool:
        return self.when(ctx)

    def run(self, conn: ServerConnection, ctx: Any) -> StepResult:
        return self.step.run(conn, ctx)

    def __getattr__(self, name: str) -> Any:
        """Expose wrapped-step attributes used by existing tests/tools."""
        return getattr(self.step, name)


@dataclass(frozen=True)
class Recipe:
    """A stable topological recipe ordered by declared resources."""

    operations: tuple[Operation, ...]
    initial: frozenset[Resource] = field(default_factory=frozenset)

    def steps(self, ctx: Any) -> list[Operation]:
        """Return active operations in dependency order."""
        remaining = [op for op in self.operations if op.active(ctx)]
        available = set(self.initial)
        ordered: list[Operation] = []

        while remaining:
            for op in list(remaining):
                if op.requires <= available:
                    ordered.append(op)
                    available.update(op.provides)
                    remaining.remove(op)
                    break
            else:
                details = []
                for op in remaining:
                    missing = sorted(str(r) for r in op.requires - available)
                    if missing:
                        details.append(f"{op.name}: missing {', '.join(missing)}")
                if not details:
                    details = [f"{op.name}: dependency cycle or no progress" for op in remaining]
                raise RecipeValidationError("Cannot order provisioning recipe: " + "; ".join(details))

        return ordered

    def validate(self, ctx: Any) -> None:
        """Raise if the active recipe cannot be ordered."""
        self.steps(ctx)


def op(
    step: Step,
    *,
    requires: Iterable[Resource] = (),
    provides: Iterable[Resource] = (),
    when: Condition = _always,
) -> Operation:
    """Small factory for readable recipe declarations."""
    return Operation(
        step=step,
        requires=frozenset(requires),
        provides=frozenset(provides),
        when=when,
    )
