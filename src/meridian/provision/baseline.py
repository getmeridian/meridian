"""Shared host-baseline provisioning recipe."""

from __future__ import annotations

import hashlib
import shlex
from dataclasses import dataclass

from meridian.provision.common import (
    AUTO_UPGRADES_CONF,
    REQUIRED_PACKAGES,
    CheckDiskSpace,
    ConfigureBBR,
    ConfigureFail2ban,
    ConfigureFirewall,
    EnableAutoUpgrades,
    EnsurePort443,
    HardenSSH,
    InstallPackages,
    SetTimezone,
)
from meridian.provision.docker import InstallDocker
from meridian.provision.recipe import Operation, Recipe, Resource, op
from meridian.provision.steps import ProvisionContext


def _harden(ctx: ProvisionContext) -> bool:
    return ctx.harden


def _not_harden(ctx: ProvisionContext) -> bool:
    return not ctx.harden


@dataclass(frozen=True)
class BaselineCheck:
    """One read-only assertion matching the shared baseline recipe."""

    name: str
    command: str


def build_server_baseline_steps(
    ctx: ProvisionContext,
    *,
    install_docker: bool,
    manage_public_ports: bool = True,
) -> list[Operation]:
    """Build the shared OS baseline, optionally including Docker."""
    extra_packages = ["fail2ban"] if ctx.harden else []
    operations = [
        op(CheckDiskSpace(), provides=[Resource.DISK_SPACE_CHECKED]),
        op(
            InstallPackages(REQUIRED_PACKAGES + extra_packages),
            requires=[Resource.DISK_SPACE_CHECKED],
            provides=[Resource.SYSTEM_PACKAGES],
        ),
        op(EnableAutoUpgrades(), requires=[Resource.SYSTEM_PACKAGES], provides=[Resource.AUTO_UPGRADES]),
        op(SetTimezone(), requires=[Resource.SYSTEM_PACKAGES], provides=[Resource.TIMEZONE_UTC]),
        op(HardenSSH(), requires=[Resource.SYSTEM_PACKAGES], provides=[Resource.SSH_HARDENED], when=_harden),
        op(
            ConfigureFail2ban(),
            requires=[Resource.SYSTEM_PACKAGES, Resource.SSH_HARDENED],
            provides=[Resource.FAIL2BAN_RUNNING],
            when=_harden,
        ),
        op(ConfigureBBR(), requires=[Resource.SYSTEM_PACKAGES], provides=[Resource.BBR_ENABLED]),
        op(
            ConfigureFirewall(manage_public_ports=manage_public_ports),
            requires=[Resource.SYSTEM_PACKAGES],
            provides=[
                Resource.FIREWALL_CONFIGURED,
                *([Resource.HTTPS_ALLOWED] if manage_public_ports else []),
            ],
            when=_harden,
        ),
    ]
    if manage_public_ports:
        operations.append(
            op(
                EnsurePort443(),
                requires=[Resource.SYSTEM_PACKAGES],
                provides=[Resource.HTTPS_ALLOWED],
                when=_not_harden,
            )
        )
    if install_docker:
        operations.append(
            op(InstallDocker(), requires=[Resource.SYSTEM_PACKAGES], provides=[Resource.DOCKER_INSTALLED])
        )

    return Recipe(tuple(operations)).steps(ctx)


def build_server_baseline_checks(
    ctx: ProvisionContext,
    *,
    install_docker: bool,
    manage_public_ports: bool = True,
) -> list[BaselineCheck]:
    """Build read-only checks covering every durable baseline effect."""
    packages = [*REQUIRED_PACKAGES, *(["fail2ban"] if ctx.harden else [])]
    package_args = " ".join(shlex.quote(package) for package in packages)
    upgrade_path = "/etc/apt/apt.conf.d/20auto-upgrades"
    upgrade_digest = hashlib.sha256(AUTO_UPGRADES_CONF.encode()).hexdigest()
    checks = [
        BaselineCheck(
            "packages",
            "test \"$(dpkg-query -W -f='${Status}\\n' "
            f"{package_args} 2>/dev/null | grep -c '^install ok installed$')\" -eq {len(packages)}",
        ),
        BaselineCheck(
            "automatic_upgrades",
            f"printf '%s  %s\\n' {shlex.quote(upgrade_digest)} {shlex.quote(upgrade_path)} "
            "| sha256sum --check --status -",
        ),
        BaselineCheck(
            "timezone",
            'test "$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null)" = UTC',
        ),
        BaselineCheck(
            "bbr",
            "if sysctl -n net.ipv4.tcp_congestion_control >/dev/null 2>&1 && "
            "sysctl -n net.core.default_qdisc >/dev/null 2>&1; then "
            'test "$(sysctl -n net.ipv4.tcp_congestion_control)" = bbr && '
            'test "$(sysctl -n net.core.default_qdisc)" = fq; fi',
        ),
    ]
    if ctx.harden:
        checks.extend(
            [
                BaselineCheck(
                    "ssh_hardening",
                    "sshd -t && sshd -T | grep -qx 'passwordauthentication no' && "
                    "sshd -T | grep -qx 'kbdinteractiveauthentication no'",
                ),
                BaselineCheck("fail2ban", "systemctl is-active --quiet fail2ban"),
                BaselineCheck(
                    "firewall",
                    "ufw status | grep -qx 'Status: active' && "
                    "ufw status verbose | grep -q '^Default: deny (incoming), allow (outgoing)' && "
                    "ports=$(sshd -T 2>/dev/null | awk '$1 == \"port\" {print $2}'); "
                    'test -n "$ports" || ports=22; rules=$(ufw show added 2>/dev/null); '
                    "for port in $ports; do printf '%s\\n' \"$rules\" | "
                    'grep -Fq "allow ${port}/tcp" || exit 1; done',
                ),
            ]
        )
    if manage_public_ports:
        checks.extend(
            [
                BaselineCheck("https_firewall", "ufw show added 2>/dev/null | grep -Fq 'allow 443/tcp'"),
                BaselineCheck("hysteria_firewall", "ufw show added 2>/dev/null | grep -Fq 'allow 443/udp'"),
            ]
        )
        if ctx.needs_web_server:
            checks.append(BaselineCheck("http_firewall", "ufw show added 2>/dev/null | grep -Fq 'allow 80/tcp'"))
    if install_docker:
        checks.append(
            BaselineCheck(
                "docker",
                "docker info >/dev/null 2>&1 && docker compose version >/dev/null 2>&1",
            )
        )
    return checks
