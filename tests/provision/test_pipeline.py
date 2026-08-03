"""Tests for build_setup_steps() pipeline assembly.

Uses the Remnawave panel/node architecture.
"""

from __future__ import annotations

import pytest

from meridian.provision import build_node_steps, build_setup_steps
from meridian.provision.steps import ProvisionContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def step_names(ctx: ProvisionContext) -> list[str]:
    """Return the list of step names for a given context."""
    return [s.name for s in build_setup_steps(ctx)]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def base_ctx() -> ProvisionContext:
    """Minimal context: no domain, no hosted page, harden off, xhttp off."""
    return ProvisionContext(
        ip="198.51.100.1",
        harden=False,
        xhttp_enabled=False,
        hosted_page=False,
        domain="",
    )


@pytest.fixture
def domain_ctx() -> ProvisionContext:
    """Context with domain mode enabled and all features on."""
    return ProvisionContext(
        ip="198.51.100.1",
        domain="example.com",
        harden=True,
        xhttp_enabled=True,
        hosted_page=False,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMinimalPipeline:
    def test_minimal_step_count(self, base_ctx: ProvisionContext):
        """Minimal config: disk check, packages, auto-upgrades, timezone, BBR,
        ensure port 443, docker, remnawave panel = 8 steps (no nginx without hosted_page)."""
        steps = build_setup_steps(base_ctx)
        names = [s.name for s in steps]
        assert len(steps) == 8, f"Expected 8 minimal steps, got {len(steps)}: {names}"

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
        # harden=True replaces EnsurePort443 with HardenSSH + ConfigureFail2ban + ConfigureFirewall (+2 net)
        assert "Harden SSH configuration" in names_with
        assert "Configure fail2ban" in names_with
        assert "Configure firewall" in names_with
        assert "Ensure port 443" not in names_with
        assert "Ensure port 443" in names_without
        assert len(names_with) == len(names_without) + 2


class TestNodePipelineHardens:
    """Redeploy of an existing node takes `build_node_steps` because
    `is_panel_host=is_first_deploy` in setup.py. Until now, that path
    silently skipped fail2ban even when the operator asked for hardening
    — the package wasn't in REQUIRED_PACKAGES, and ConfigureFail2ban
    wasn't appended. This regression test pins both."""

    def test_node_pipeline_no_harden_has_required_packages_only(self, base_ctx: ProvisionContext) -> None:
        steps = build_node_steps(base_ctx)
        install_step = next(s for s in steps if s.name == "Install system packages")
        assert install_step._packages is not None  # not None (not collapsed by precedence bug)
        assert "fail2ban" not in install_step._packages

    def test_node_pipeline_harden_installs_fail2ban(self, base_ctx: ProvisionContext) -> None:
        base_ctx.harden = True
        steps = build_node_steps(base_ctx)
        install_step = next(s for s in steps if s.name == "Install system packages")
        assert install_step._packages is not None
        assert "fail2ban" in install_step._packages

    def test_node_pipeline_harden_configures_fail2ban(self, base_ctx: ProvisionContext) -> None:
        base_ctx.harden = True
        step_names_list = [s.name for s in build_node_steps(base_ctx)]
        assert "Configure fail2ban" in step_names_list
        # Order: HardenSSH → ConfigureFail2ban → ConfigureBBR
        assert step_names_list.index("Harden SSH configuration") < step_names_list.index("Configure fail2ban")
        assert step_names_list.index("Configure fail2ban") < step_names_list.index("Enable BBR congestion control")

    def test_node_pipeline_no_harden_skips_fail2ban_step(self, base_ctx: ProvisionContext) -> None:
        step_names_list = [s.name for s in build_node_steps(base_ctx)]
        assert "Configure fail2ban" not in step_names_list


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


class TestHostedPage:
    def test_hosted_page_adds_services(self, base_ctx: ProvisionContext):
        base_ctx.hosted_page = True
        names = step_names(base_ctx)
        assert "Install nginx" in names
        assert "Configure nginx" in names
        assert "Issue TLS certificate" in names
        assert "Deploy PWA assets" in names


class TestDomainMode:
    def test_domain_mode_adds_services(self):
        ctx = ProvisionContext(
            ip="198.51.100.1",
            domain="example.com",
            harden=False,
            xhttp_enabled=False,
            hosted_page=False,
        )
        names = step_names(ctx)
        assert "Install nginx" in names
        assert "Configure nginx" in names
        assert "Issue TLS certificate" in names
        assert "Deploy PWA assets" in names


class TestFullPipeline:
    def test_full_pipeline_has_remnawave_steps(self, domain_ctx: ProvisionContext):
        """Verify the full pipeline includes Remnawave panel step."""
        names = step_names(domain_ctx)
        assert "Deploy Remnawave panel" in names
        assert "Install Docker" in names
        assert "Install nginx" in names
        assert "Issue TLS certificate" in names


class TestStepOrdering:
    def test_step_ordering(self, domain_ctx: ProvisionContext):
        names = step_names(domain_ctx)

        def assert_before(a: str, b: str) -> None:
            assert a in names, f"{a!r} not found in steps"
            assert b in names, f"{b!r} not found in steps"
            assert names.index(a) < names.index(b), f"{a!r} should come before {b!r}, got order: {names}"

        # Packages before Docker
        assert_before("Install system packages", "Install Docker")
        # Docker before Remnawave
        assert_before("Install Docker", "Deploy Remnawave panel")
        # Remnawave before nginx
        assert_before("Deploy Remnawave panel", "Install nginx")
        assert_before("Install nginx", "Configure nginx")
        assert_before("Configure nginx", "Issue TLS certificate")
        assert_before("Issue TLS certificate", "Deploy PWA assets")


class TestNodeOnlyPipeline:
    def test_node_only_no_panel(self, base_ctx: ProvisionContext):
        """When is_panel_host is False, panel step is skipped."""
        base_ctx.is_panel_host = False
        names = step_names(base_ctx)
        assert "Deploy Remnawave panel" not in names
