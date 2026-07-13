"""Provisioning engine — deploys and configures proxy servers via SSH.

The build_setup_steps() function assembles the full deployment pipeline:
  common -> docker -> remnawave (panel + node) -> nginx -> connection page

Uses the Remnawave panel/node architecture.
"""

from __future__ import annotations

from meridian.provision.progress import NoopStepRenderer, RichStepRenderer, StepRenderer
from meridian.provision.recipe import Operation, Recipe, RecipeValidationError, Resource, op
from meridian.provision.steps import ProvisionContext, Provisioner, Step, StepContext, StepResult

__all__ = [
    "NoopStepRenderer",
    "Operation",
    "Provisioner",
    "ProvisionContext",
    "Recipe",
    "RecipeValidationError",
    "Resource",
    "RichStepRenderer",
    "Step",
    "StepContext",
    "StepRenderer",
    "StepResult",
    "build_node_steps",
    "build_setup_steps",
]


def _needs_web_server(ctx: ProvisionContext) -> bool:
    return ctx.needs_web_server


def _domain_mode(ctx: ProvisionContext) -> bool:
    return ctx.needs_web_server and ctx.domain_mode


def _ip_web_mode(ctx: ProvisionContext) -> bool:
    return ctx.needs_web_server and not ctx.domain_mode


def _harden(ctx: ProvisionContext) -> bool:
    return ctx.harden


def _not_harden(ctx: ProvisionContext) -> bool:
    return not ctx.harden


def _panel_host(ctx: ProvisionContext) -> bool:
    return ctx.is_panel_host


def _warp(ctx: ProvisionContext) -> bool:
    return ctx.warp


def build_setup_steps(ctx: ProvisionContext) -> list[Operation]:
    """Assemble the full deployment recipe.

    Steps declare resource contracts and the recipe graph derives the
    execution order for the active operations in the current context.
    """
    from meridian.provision.common import (
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
    from meridian.provision.remnawave_panel import DeployRemnawavePanel

    # Python operator precedence on `+` vs `if-else` is a trap here:
    # `A + B if cond else None` parses as `(A + B) if cond else None`,
    # which silently yields `InstallPackages(None)` when not hardening —
    # meaning REQUIRED_PACKAGES never get installed at all. Explicit form:
    extra_pkgs = ["fail2ban"] if ctx.harden else []

    operations = [
        # -- Pre-flight --
        op(CheckDiskSpace(), provides=[Resource.DISK_SPACE_CHECKED]),
        # -- Common (OS-level setup) --
        op(
            InstallPackages(REQUIRED_PACKAGES + extra_pkgs),
            requires=[Resource.DISK_SPACE_CHECKED],
            provides=[Resource.SYSTEM_PACKAGES],
        ),
        op(EnableAutoUpgrades(), requires=[Resource.SYSTEM_PACKAGES], provides=[Resource.AUTO_UPGRADES]),
        op(SetTimezone(), requires=[Resource.SYSTEM_PACKAGES], provides=[Resource.TIMEZONE_UTC]),
        # Server hardening (optional — skip for shared servers with existing services)
        op(HardenSSH(), requires=[Resource.SYSTEM_PACKAGES], provides=[Resource.SSH_HARDENED], when=_harden),
        op(
            ConfigureFail2ban(),
            requires=[Resource.SYSTEM_PACKAGES, Resource.SSH_HARDENED],
            provides=[Resource.FAIL2BAN_RUNNING],
            when=_harden,
        ),
        op(ConfigureBBR(), requires=[Resource.SYSTEM_PACKAGES], provides=[Resource.BBR_ENABLED]),
        op(
            ConfigureFirewall(),
            requires=[Resource.SYSTEM_PACKAGES],
            provides=[Resource.FIREWALL_CONFIGURED, Resource.HTTPS_ALLOWED],
            when=_harden,
        ),
        # Even without --harden, ensure port 443 is allowed if ufw is active.
        op(EnsurePort443(), requires=[Resource.SYSTEM_PACKAGES], provides=[Resource.HTTPS_ALLOWED], when=_not_harden),
        # -- Docker --
        op(InstallDocker(), requires=[Resource.SYSTEM_PACKAGES], provides=[Resource.DOCKER_INSTALLED]),
    ]

    # -- Remnawave panel (node deployed after API setup, not here) --
    operations.append(
        op(
            DeployRemnawavePanel(),
            requires=[Resource.DOCKER_INSTALLED],
            provides=[Resource.REMNAWAVE_PANEL_RUNNING],
            when=_panel_host,
        )
    )

    # -- WARP client (optional) --
    from meridian.provision.warp import InstallWarp

    operations.append(
        op(InstallWarp(), requires=[Resource.SYSTEM_PACKAGES], provides=[Resource.WARP_CONNECTED], when=_warp)
    )

    # -- nginx + TLS + connection page --
    from meridian.provision.nginx import ConfigureNginx, InstallNginx
    from meridian.provision.tls import IssueTLSCert

    operations.extend(
        [
            op(
                InstallNginx(),
                requires=[Resource.SYSTEM_PACKAGES],
                provides=[Resource.NGINX_INSTALLED],
                when=_needs_web_server,
            ),
            op(
                ConfigureNginx(domain=ctx.domain, reality_backend_port=ctx.reality_port),
                requires=[Resource.NGINX_INSTALLED],
                provides=[Resource.NGINX_CONFIGURED],
                when=_domain_mode,
            ),
            op(
                ConfigureNginx(
                    domain="",
                    ip_mode=True,
                    server_ip=ctx.ip,
                    reality_backend_port=ctx.reality_port,
                ),
                requires=[Resource.NGINX_INSTALLED],
                provides=[Resource.NGINX_CONFIGURED],
                when=_ip_web_mode,
            ),
            op(
                IssueTLSCert(domain=ctx.domain),
                requires=[Resource.NGINX_CONFIGURED],
                provides=[Resource.TLS_CERTIFICATE],
                when=_domain_mode,
            ),
            op(
                IssueTLSCert(domain="", ip_mode=True, server_ip=ctx.ip),
                requires=[Resource.NGINX_CONFIGURED],
                provides=[Resource.TLS_CERTIFICATE],
                when=_ip_web_mode,
            ),
        ]
    )

    # PWA assets (connection pages deployed via post-provisioner API setup)
    from meridian.provision.nginx import DeployPWAAssets

    operations.append(
        op(
            DeployPWAAssets(),
            requires=[Resource.TLS_CERTIFICATE],
            provides=[Resource.PWA_ASSETS],
            when=_needs_web_server,
        )
    )

    return Recipe(tuple(operations)).steps(ctx)


def build_node_steps(ctx: ProvisionContext) -> list[Operation]:
    """Assemble the recipe for adding a node-only server (no panel).

    Used by `meridian node add <IP>`.
    """
    from meridian.provision.common import (
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

    extra_pkgs = ["fail2ban"] if ctx.harden else []

    operations = [
        op(CheckDiskSpace(), provides=[Resource.DISK_SPACE_CHECKED]),
        op(
            InstallPackages(REQUIRED_PACKAGES + extra_pkgs),
            requires=[Resource.DISK_SPACE_CHECKED],
            provides=[Resource.SYSTEM_PACKAGES],
        ),
        op(EnableAutoUpgrades(), requires=[Resource.SYSTEM_PACKAGES], provides=[Resource.AUTO_UPGRADES]),
        op(SetTimezone(), requires=[Resource.SYSTEM_PACKAGES], provides=[Resource.TIMEZONE_UTC]),
        op(HardenSSH(), requires=[Resource.SYSTEM_PACKAGES], provides=[Resource.SSH_HARDENED], when=_harden),
        # Mirror `build_setup_steps` — without this, redeploys (which take
        # the node-only path because `is_panel_host=is_first_deploy`) never
        # configure fail2ban even when the operator asked for hardening.
        op(
            ConfigureFail2ban(),
            requires=[Resource.SYSTEM_PACKAGES, Resource.SSH_HARDENED],
            provides=[Resource.FAIL2BAN_RUNNING],
            when=_harden,
        ),
        op(ConfigureBBR(), requires=[Resource.SYSTEM_PACKAGES], provides=[Resource.BBR_ENABLED]),
        op(
            ConfigureFirewall(),
            requires=[Resource.SYSTEM_PACKAGES],
            provides=[Resource.FIREWALL_CONFIGURED, Resource.HTTPS_ALLOWED],
            when=_harden,
        ),
        op(EnsurePort443(), requires=[Resource.SYSTEM_PACKAGES], provides=[Resource.HTTPS_ALLOWED], when=_not_harden),
        op(InstallDocker(), requires=[Resource.SYSTEM_PACKAGES], provides=[Resource.DOCKER_INSTALLED]),
        # Node deployed after API setup (setup.py), not in pipeline
    ]

    from meridian.provision.nginx import ConfigureNginx, InstallNginx
    from meridian.provision.tls import IssueTLSCert

    operations.extend(
        [
            op(
                InstallNginx(),
                requires=[Resource.SYSTEM_PACKAGES],
                provides=[Resource.NGINX_INSTALLED],
                when=_needs_web_server,
            ),
            op(
                ConfigureNginx(domain=ctx.domain, reality_backend_port=ctx.reality_port),
                requires=[Resource.NGINX_INSTALLED],
                provides=[Resource.NGINX_CONFIGURED],
                when=_domain_mode,
            ),
            op(
                ConfigureNginx(
                    domain="",
                    ip_mode=True,
                    server_ip=ctx.ip,
                    reality_backend_port=ctx.reality_port,
                ),
                requires=[Resource.NGINX_INSTALLED],
                provides=[Resource.NGINX_CONFIGURED],
                when=_ip_web_mode,
            ),
            op(
                IssueTLSCert(domain=ctx.domain),
                requires=[Resource.NGINX_CONFIGURED],
                provides=[Resource.TLS_CERTIFICATE],
                when=_domain_mode,
            ),
            op(
                IssueTLSCert(domain="", ip_mode=True, server_ip=ctx.ip),
                requires=[Resource.NGINX_CONFIGURED],
                provides=[Resource.TLS_CERTIFICATE],
                when=_ip_web_mode,
            ),
        ]
    )

    return Recipe(tuple(operations)).steps(ctx)
