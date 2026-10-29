"""Tests for build_setup_steps(): the control bootstrap's recipe."""

from __future__ import annotations

import pytest

from meridian.provision import build_setup_steps
from meridian.provision.steps import ProvisionContext
from tests.support.control_service import bootstrap_context


def step_names(ctx: ProvisionContext) -> list[str]:
    """Return the list of step names for a given context."""
    return [s.name for s in build_setup_steps(ctx)]


@pytest.fixture
def base_ctx() -> ProvisionContext:
    """Minimal context: no domain, no hosted page, harden off, xhttp off."""
    return bootstrap_context(harden=False, xhttp_enabled=False, hosted_page=False, domain="")


@pytest.fixture
def domain_ctx() -> ProvisionContext:
    """Context with domain mode enabled and all features on."""
    return bootstrap_context(domain="example.com", harden=True, xhttp_enabled=True, hosted_page=False)


def test_the_recipe_needs_the_service_install() -> None:
    with pytest.raises(ValueError, match="control service"):
        build_setup_steps(ProvisionContext(ip="198.51.100.1"))


class TestMinimalPipeline:
    def test_minimal_steps_run_the_baseline_then_the_service(self, base_ctx: ProvisionContext):
        """No nginx without hosted_page or a domain; the plan owns public ports, and the service needs no Docker."""
        names = step_names(base_ctx)
        assert names[-1] == "Install control service"
        assert "Install Docker" not in names
        assert "Add swap space" not in names

    def test_no_services_without_domain_or_hosted_page(self, base_ctx: ProvisionContext):
        names = step_names(base_ctx)
        assert "Install nginx" not in names
        assert "Configure nginx" not in names
        assert "Issue TLS certificate" not in names
        assert "Deploy connection page" not in names


class TestHardenSteps:
    def test_harden_adds_ssh_and_firewall(self, base_ctx: ProvisionContext):
        names_without = step_names(base_ctx)
        base_ctx.harden = True
        names_with = step_names(base_ctx)
        # harden=True adds HardenSSH + ConfigureFail2ban + ConfigureFirewall.
        assert "Harden SSH configuration" in names_with
        assert "Configure fail2ban" in names_with
        assert "Configure firewall" in names_with
        assert len(names_with) == len(names_without) + 3


class TestPipelineHardens:
    def test_harden_configures_fail2ban_after_ssh_and_before_bbr(self, base_ctx: ProvisionContext) -> None:
        base_ctx.harden = True
        step_names_list = step_names(base_ctx)
        assert "Configure fail2ban" in step_names_list
        assert step_names_list.index("Harden SSH configuration") < step_names_list.index("Configure fail2ban")
        assert step_names_list.index("Configure fail2ban") < step_names_list.index("Enable BBR congestion control")

    def test_no_harden_skips_fail2ban_step(self, base_ctx: ProvisionContext) -> None:
        assert "Configure fail2ban" not in step_names(base_ctx)


class TestSetupPipelinePackages:
    """Guards against the operator-precedence trap in `build_setup_steps`
    where `REQUIRED_PACKAGES + ["fail2ban"] if ctx.harden else None` parses
    as `(REQUIRED + fail2ban) if ctx.harden else None`, yielding
    InstallPackages(None) for the non-hardening path."""

    def test_setup_pipeline_no_harden_still_installs_required(self, base_ctx: ProvisionContext) -> None:
        steps = build_setup_steps(base_ctx)
        install_step = next(s for s in steps if s.name == "Install system packages")
        assert install_step._packages is not None, (
            "REQUIRED_PACKAGES must be installed even without --harden; "
            "operator-precedence bug would collapse this to None"
        )
        assert "fail2ban" not in install_step._packages

    def test_setup_pipeline_harden_installs_fail2ban(self, base_ctx: ProvisionContext) -> None:
        base_ctx.harden = True
        steps = build_setup_steps(base_ctx)
        install_step = next(s for s in steps if s.name == "Install system packages")
        assert install_step._packages is not None
        assert "fail2ban" in install_step._packages


@pytest.mark.parametrize(
    "fields",
    [{"hosted_page": True}, {"domain": "example.com"}],
    ids=["hosted-page", "domain"],
)
def test_a_web_server_adds_nginx_tls_and_pwa_assets(fields: dict) -> None:
    names = step_names(bootstrap_context(harden=False, xhttp_enabled=False, **fields))
    assert {"Install nginx", "Configure nginx", "Issue TLS certificate", "Deploy PWA assets"} <= set(names)


def test_step_ordering(domain_ctx: ProvisionContext) -> None:
    names = step_names(domain_ctx)
    order = [
        "Install system packages",
        "Install control service",
        "Install nginx",
        "Configure nginx",
        "Issue TLS certificate",
        "Deploy PWA assets",
    ]
    assert [name for name in names if name in order] == order
