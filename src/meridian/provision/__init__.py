"""Provisioning engine — deploys and configures proxy servers via SSH.

build_setup_steps() assembles the control-server bootstrap:
  swap -> baseline -> docker -> remnawave panel -> nginx -> TLS -> PWA assets

Uses the Remnawave panel/node architecture.
"""

from __future__ import annotations

from meridian.provision.baseline import BaselineCheck, build_server_baseline_checks, build_server_baseline_steps
from meridian.provision.progress import NoopStepRenderer, RichStepRenderer, StepRenderer
from meridian.provision.steps import ProvisionContext, Provisioner, Step, StepContext, StepResult

__all__ = [
    "NoopStepRenderer",
    "BaselineCheck",
    "Provisioner",
    "ProvisionContext",
    "RichStepRenderer",
    "Step",
    "StepContext",
    "StepRenderer",
    "StepResult",
    "build_server_baseline_checks",
    "build_server_baseline_steps",
    "build_setup_steps",
]


def build_setup_steps(ctx: ProvisionContext) -> list[Step]:
    """Assemble the control-server bootstrap in execution order.

    The reviewed plan's tagged firewall rules own every public port; the
    bootstrap must not add untagged ones (it used to open 443/udp always).
    """
    from meridian.provision.common import EnsureSwap
    from meridian.provision.nginx import ConfigureNginx, DeployPWAAssets, InstallNginx
    from meridian.provision.remnawave_panel import DeployRemnawavePanel
    from meridian.provision.tls import IssueTLSCert

    steps: list[Step]
    if ctx.control_service is not None:
        from meridian.provision.control_service import InstallControlService

        # Meridian's own service needs neither Docker nor swap: it is one small Python process.
        steps = [*build_server_baseline_steps(ctx, install_docker=False), InstallControlService(ctx.control_service)]
    else:
        # Swap comes first: a retry after a starved panel start finds the panel already running.
        # The Remnawave node is deployed after API setup, not here.
        steps = [EnsureSwap(), *build_server_baseline_steps(ctx, install_docker=True), DeployRemnawavePanel()]
    if not ctx.needs_web_server:
        return steps
    service = ctx.control_service is not None
    if ctx.domain_mode:
        configure: Step = ConfigureNginx(
            domain=ctx.domain, reality_backend_port=ctx.reality_port, control_service=service
        )
        certificate: Step = IssueTLSCert(domain=ctx.domain)
    else:
        configure = ConfigureNginx(
            domain="",
            ip_mode=True,
            server_ip=ctx.ip,
            reality_backend_port=ctx.reality_port,
            control_service=service,
        )
        certificate = IssueTLSCert(domain="", ip_mode=True, server_ip=ctx.ip)
    # PWA assets only; connection pages are deployed by post-provisioner API setup.
    return [*steps, InstallNginx(), configure, certificate, DeployPWAAssets()]
