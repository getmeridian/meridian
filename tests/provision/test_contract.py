"""Tests for inter-step data contracts in the provisioner pipeline.

These tests verify that the data each step writes to ctx is sufficient
for all downstream steps that read from ctx. They catch bugs like:
- A step forgets to set a context key that a downstream step needs
- A step reads ctx["key"] (hard crash) instead of ctx.get("key")
- A constructor argument isn't passed from build_setup_steps()
- Port/path values don't flow correctly between steps

4.0: Updated for Remnawave pipeline (replaces 3x-ui).
"""

from __future__ import annotations

from pathlib import Path

from meridian.provision import build_setup_steps
from meridian.provision.relay import RelayContext, build_relay_steps
from meridian.provision.steps import ProvisionContext
from tests.support.mock_connection import MockConnection


def _make_pipeline_conn() -> MockConnection:
    """Create a MockConnection with responses for a full pipeline run.

    Returns plausible success responses for every command the pipeline
    might issue. The goal is NOT to test each step's logic but to verify
    the data contract: every key that step N writes to ctx is present
    for step N+1.
    """
    conn = MockConnection()

    # InstallPackages: all present
    conn.when("dpkg-query -W", stdout="curl\tok\nwget\tok\nsocat\tok\n")

    # EnableAutoUpgrades: already configured
    conn.when(
        "cat /etc/apt/apt.conf.d/20auto-upgrades",
        stdout='APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";',
    )

    # SetTimezone: already UTC
    conn.when("timedatectl show", stdout="Timezone=UTC\n")

    # HardenSSH: already hardened
    conn.when("grep -qE", rc=0)
    conn.when("grep -c", stdout="0\n")

    # ConfigureBBR
    conn.when("sysctl -n net.core.default_qdisc", stdout="fq\n")
    conn.when("sysctl -n net.ipv4.tcp_congestion_control", stdout="bbr\n")

    # ConfigureFirewall
    conn.when("which ufw", stdout="/usr/sbin/ufw\n")
    conn.when(
        "ufw status",
        stdout="Status: active\n\n22/tcp  ALLOW  Anywhere\n443/tcp ALLOW  Anywhere\n80/tcp  ALLOW  Anywhere\n",
    )

    # InstallDocker: already installed
    conn.when("docker --version", stdout="Docker version 27.0.0\n")
    conn.when("docker ps -q", stdout="abc123\n")

    # Remnawave panel: already running
    conn.when("docker inspect", stdout='[{"State":{"Status":"running"}}]')
    conn.when("docker compose", rc=0)
    conn.when(
        "cat /opt/remnawave/.env",
        stdout="REMNAWAVE_JWT_AUTH_SECRET=test\nREMNAWAVE_JWT_API_SECRET=test\nREMNAWAVE_DB_PASSWORD=test\n",
    )

    # Remnawave node: already running
    conn.when("ss -tlnp", stdout="")

    # nginx
    conn.when("dpkg -l nginx", stdout="ii  nginx\n")
    conn.when("nginx -t", rc=0)
    conn.when("systemctl", rc=0)

    # Connection pages
    conn.when("printf", rc=0)
    conn.when("mkdir", rc=0)
    conn.when("chown", rc=0)
    conn.when("crontab", rc=0)
    conn.when("python3", rc=0)

    # Xray verify
    conn.when("pgrep", stdout="12345\n")
    conn.when("curl", stdout="200")

    return conn


class TestFullPipelineContract:
    """Verify the data contract across the full deploy pipeline."""

    def test_standalone_pipeline_no_keyerror(self, tmp_path: Path) -> None:
        """Full standalone pipeline (no domain) runs without KeyError."""
        ctx = ProvisionContext(
            ip="198.51.100.1",
            xhttp_enabled=True,
            hosted_page=True,
            creds_dir=str(tmp_path / "creds"),
        )
        ctx.xhttp_port = 31589
        ctx.reality_port = 10589

        conn = _make_pipeline_conn()
        steps = build_setup_steps(ctx)

        for step in steps:
            try:
                step.run(conn, ctx)
            except KeyError as e:
                raise AssertionError(
                    f"Step '{step.name}' crashed with KeyError: {e}. Context keys available: {list(ctx._state.keys())}"
                ) from e
            except AttributeError as e:
                raise AssertionError(
                    f"Step '{step.name}' crashed with AttributeError: {e}. "
                    f"Likely a missing or wrong-typed context value."
                ) from e

    def test_domain_pipeline_no_keyerror(self, tmp_path: Path) -> None:
        """Full domain-mode pipeline runs without KeyError."""
        ctx = ProvisionContext(
            ip="198.51.100.1",
            domain="example.com",
            xhttp_enabled=True,
            hosted_page=True,
            creds_dir=str(tmp_path / "creds"),
        )
        ctx.xhttp_port = 31589
        ctx.reality_port = 10589
        ctx.wss_port = 21589

        conn = _make_pipeline_conn()
        steps = build_setup_steps(ctx)

        for step in steps:
            try:
                step.run(conn, ctx)
            except KeyError as e:
                raise AssertionError(
                    f"Step '{step.name}' crashed with KeyError: {e}. Context keys: {list(ctx._state.keys())}"
                ) from e
            except AttributeError as e:
                raise AssertionError(f"Step '{step.name}' crashed with AttributeError: {e}.") from e

    def test_no_harden_pipeline_no_keyerror(self, tmp_path: Path) -> None:
        """Pipeline with harden=False still has all needed context keys."""
        ctx = ProvisionContext(
            ip="198.51.100.1",
            harden=False,
            hosted_page=True,
            creds_dir=str(tmp_path / "creds"),
        )
        ctx.xhttp_port = 31589
        ctx.reality_port = 10589

        conn = _make_pipeline_conn()
        steps = build_setup_steps(ctx)

        for step in steps:
            try:
                step.run(conn, ctx)
            except (KeyError, AttributeError) as e:
                raise AssertionError(f"Step '{step.name}' crashed: {e}. Context keys: {list(ctx._state.keys())}") from e


class TestRelayPipelineContract:
    """Verify data contract across the relay pipeline."""

    def test_relay_pipeline_no_keyerror(self) -> None:
        """Full relay pipeline runs without KeyError."""
        ctx = RelayContext(
            relay_ip="198.51.100.10",
            exit_ip="198.51.100.1",
            listen_port=9443,
        )

        conn = MockConnection()
        conn.when("dpkg-query", stdout="curl\tok\nwget\tok\n")
        conn.when("sysctl -n net.core.default_qdisc", stdout="fq\n")
        conn.when("sysctl -n net.ipv4.tcp_congestion_control", stdout="bbr\n")
        conn.when("which ufw", stdout="/usr/sbin/ufw\n")
        conn.when("ufw status", stdout="Status: active\n")
        conn.when("realm --version", stdout="realm 2.9.3\n")
        conn.when("uname -m", stdout="x86_64\n")
        conn.when("systemctl is-active", stdout="active\n")
        conn.when("nc -z", rc=0)

        steps = build_relay_steps(ctx)

        for step in steps:
            try:
                step.run(conn, ctx)
            except (KeyError, AttributeError) as e:
                raise AssertionError(f"Relay step '{step.name}' crashed: {e}") from e
