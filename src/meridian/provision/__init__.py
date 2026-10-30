"""Provisioning engine — deploys and configures proxy servers via SSH.

build_setup_steps() assembles the control-server bootstrap:
  baseline -> control service -> nginx -> TLS
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
    from meridian.provision.control_service import InstallControlService
    from meridian.provision.nginx import ConfigureNginx, InstallNginx
    from meridian.provision.tls import IssueTLSCert

    if ctx.control_service is None:
        raise ValueError("The control bootstrap needs the control service's install.")
    # The service needs neither Docker nor swap: it is one small Python process.
    steps: list[Step] = [
        *build_server_baseline_steps(ctx, install_docker=False),
        InstallControlService(ctx.control_service),
    ]
    if not ctx.needs_web_server:
        return steps
    if ctx.domain_mode:
        configure: Step = ConfigureNginx(domain=ctx.domain, reality_backend_port=ctx.reality_port)
        certificate: Step = IssueTLSCert(domain=ctx.domain)
    else:
        configure = ConfigureNginx(
            domain="",
            ip_mode=True,
            server_ip=ctx.ip,
            reality_backend_port=ctx.reality_port,
        )
        certificate = IssueTLSCert(domain="", ip_mode=True, server_ip=ctx.ip)
    return [*steps, InstallNginx(), configure, certificate]
