"""Provisioning engine — deploys and configures proxy servers via SSH.

The build_setup_steps() function assembles the full deployment pipeline:
  common -> docker -> remnawave (panel + node) -> nginx -> connection page

Uses the Remnawave panel/node architecture.
"""

from __future__ import annotations

from meridian.provision.baseline import BaselineCheck, build_server_baseline_checks, build_server_baseline_steps
from meridian.provision.progress import NoopStepRenderer, RichStepRenderer, StepRenderer
from meridian.provision.recipe import Operation, Recipe, RecipeValidationError, Resource, op
from meridian.provision.steps import ProvisionContext, Provisioner, Step, StepContext, StepResult

__all__ = [
    "NoopStepRenderer",
    "BaselineCheck",
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
    "build_server_baseline_checks",
    "build_server_baseline_steps",
    "build_setup_steps",
]


def _needs_web_server(ctx: ProvisionContext) -> bool:
    return ctx.needs_web_server


def _domain_mode(ctx: ProvisionContext) -> bool:
    return ctx.needs_web_server and ctx.domain_mode


def _ip_web_mode(ctx: ProvisionContext) -> bool:
    return ctx.needs_web_server and not ctx.domain_mode


def build_setup_steps(ctx: ProvisionContext) -> list[Operation]:
    """Assemble the full deployment recipe.

    Steps declare resource contracts and the recipe graph derives the
    execution order for the active operations in the current context.
    """
    from meridian.provision.remnawave_panel import DeployRemnawavePanel

    # The reviewed plan's tagged firewall rules own every public port; the
    # bootstrap must not add untagged ones (it used to open 443/udp always).
    operations = build_server_baseline_steps(ctx, install_docker=True)

    # -- Remnawave panel (node deployed after API setup, not here) --
    operations.append(
        op(
            DeployRemnawavePanel(),
            requires=[Resource.DOCKER_INSTALLED],
            provides=[Resource.REMNAWAVE_PANEL_RUNNING],
        )
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
