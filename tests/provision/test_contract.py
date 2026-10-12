"""Tests for inter-step data contracts in the provisioner pipeline.

These tests verify that the data each step writes to ctx is sufficient
for all downstream steps that read from ctx. They catch bugs like:
- A step forgets to set a context key that a downstream step needs
- A step reads ctx["key"] (hard crash) instead of ctx.get("key")
- A constructor argument isn't passed from build_setup_steps()
- Port/path values don't flow correctly between steps

The approach: run the full pipeline with a MockConnection that returns
plausible success responses for every command. If any step crashes with
KeyError or AttributeError, the contract is broken.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from meridian.provision import build_setup_steps
from meridian.provision.relay import RelayContext, build_relay_steps
from meridian.provision.steps import ProvisionContext

from .conftest import MockConnection, make_credentials

# Steps that need a mock PanelClient in ctx["panel"]
_PANEL_STEPS = {
    "Log in to panel",
    "Create Reality inbound",
    "Create XHTTP inbound",
    "Create WSS inbound",
    "Disable Xray logs",
}


def _make_panel_mock() -> MagicMock:
    """Create a mock PanelClient that returns success for all API calls."""
    panel = MagicMock()
    panel.find_inbound.return_value = None  # no existing inbound
    panel.api_post_json.return_value = {"success": True}
    panel.api_post_empty.return_value = {"success": True, "obj": "{}"}
    panel.api_post_form.return_value = {"success": True}
    panel.login.return_value = None
    panel.cleanup.return_value = None
    panel.list_inbounds.return_value = []
    return panel


def _make_pipeline_conn() -> MockConnection:
    """Create a MockConnection with responses for a full pipeline run.

    Returns plausible success responses for every command the pipeline
    might issue. The goal is NOT to test each step's logic (that's in
    test_common.py etc.) but to verify the data contract: every key
    that step N writes to ctx is present for step N+1.
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
    conn.when("grep -qE", rc=0)  # PasswordAuthentication no
    conn.when("grep -c", stdout="0\n")  # no password auth lines

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

    # Deploy3xui: already running
    conn.when("docker compose", rc=0)
    conn.when("docker ps --filter", stdout="abc123\n")
    conn.when("ss -tlnp sport = :2053", stdout="")  # no port conflict

    # ConfigurePanel: discover xray binary, generate keys
    # Order matters: x25519/uuid patterns must come BEFORE the binary
    # discovery pattern, since "xray-linux-amd64 x25519" contains both.
    conn.when(
        "x25519",
        stdout="Private key: aKXt4scJxSv_5U8UrmAdEE\nPublic key: y94uzCjO78s33aWdr955amJEjL\n",
    )
    conn.when("uuid", stdout="550e8400-e29b-41d4-a716-446655440000\n")
    conn.when("xray-linux", stdout="/app/bin/xray-linux-amd64\n")
    conn.when("docker restart", rc=0)
    conn.when("curl -s -o /dev/null -w", stdout="200")

    # Xray verify
    conn.when("pgrep", stdout="12345\n")

    # nginx
    conn.when("dpkg -l nginx", stdout="ii  nginx\n")
    conn.when("nginx -t", rc=0)
    conn.when("systemctl", rc=0)

    # DeployConnectionPage
    conn.when("printf", rc=0)
    conn.when("mkdir", rc=0)
    conn.when("chown", rc=0)
    conn.when("crontab", rc=0)
    conn.when("python3", rc=0)

    return conn


class TestFullPipelineContract:
    """Verify the data contract across the full deploy pipeline.

    These tests run all steps in order and assert no KeyError/AttributeError
    from missing context keys. They don't test step logic — just that the
    data flows correctly between steps.
    """

    def test_standalone_pipeline_no_keyerror(self, tmp_path: Path) -> None:
        """Full standalone pipeline (no domain) runs without KeyError."""
        ctx = ProvisionContext(
            ip="198.51.100.1",
            xhttp_enabled=True,
            hosted_page=True,
            creds_dir=str(tmp_path / "creds"),
        )
        ctx.panel_port = 2053
        ctx.xhttp_port = 31589
        ctx.reality_port = 10589
        ctx["first_client_name"] = "default"

        conn = _make_pipeline_conn()
        steps = build_setup_steps(ctx)

        # Run each step. ConfigurePanel needs special handling since it
        # writes credentials to disk and calls the panel API.
        for step in steps:
            # Inject mock panel for steps that need it
            if step.name in _PANEL_STEPS:
                if "panel" not in ctx:
                    ctx["panel"] = _make_panel_mock()

            # ConfigurePanel needs a writable creds path
            if step.name == "Configure panel":
                (tmp_path / "creds").mkdir(parents=True, exist_ok=True)

            try:
                result = step.run(conn, ctx)
            except KeyError as e:
                raise AssertionError(
                    f"Step '{step.name}' crashed with KeyError: {e}. "
                    f"A previous step didn't set this context key. "
                    f"Context keys available: {list(ctx._state.keys())}"
                ) from e
            except AttributeError as e:
                raise AssertionError(
                    f"Step '{step.name}' crashed with AttributeError: {e}. "
                    f"Likely a missing or wrong-typed context value."
                ) from e

            # If the step failed, that's fine for contract testing —
            # we only care about KeyError/AttributeError
            if result.status == "failed":
                # For panel-dependent steps, inject credentials so
                # downstream steps don't fail on missing keys
                if "credentials" not in ctx:
                    creds = make_credentials()
                    ctx["credentials"] = creds
                    ctx["panel_configured"] = True

    def test_domain_pipeline_no_keyerror(self, tmp_path: Path) -> None:
        """Full domain-mode pipeline runs without KeyError."""
        ctx = ProvisionContext(
            ip="198.51.100.1",
            domain="example.com",
            xhttp_enabled=True,
            hosted_page=True,
            creds_dir=str(tmp_path / "creds"),
        )
        ctx.panel_port = 2053
        ctx.xhttp_port = 31589
        ctx.reality_port = 10589
        ctx.wss_port = 21589
        ctx["first_client_name"] = "default"

        conn = _make_pipeline_conn()
        steps = build_setup_steps(ctx)

        for step in steps:
            if step.name in _PANEL_STEPS:
                if "panel" not in ctx:
                    ctx["panel"] = _make_panel_mock()

            if step.name == "Configure panel":
                (tmp_path / "creds").mkdir(parents=True, exist_ok=True)

            try:
                result = step.run(conn, ctx)
            except KeyError as e:
                raise AssertionError(
                    f"Step '{step.name}' crashed with KeyError: {e}. Context keys: {list(ctx._state.keys())}"
                ) from e
            except AttributeError as e:
                raise AssertionError(f"Step '{step.name}' crashed with AttributeError: {e}.") from e

            if result.status == "failed" and "credentials" not in ctx:
                ctx["credentials"] = make_credentials()
                ctx["panel_configured"] = True

    def test_no_harden_pipeline_no_keyerror(self, tmp_path: Path) -> None:
        """Pipeline with harden=False still has all needed context keys."""
        ctx = ProvisionContext(
            ip="198.51.100.1",
            harden=False,
            hosted_page=True,
            creds_dir=str(tmp_path / "creds"),
        )
        ctx.panel_port = 2053
        ctx.xhttp_port = 31589
        ctx.reality_port = 10589
        ctx["first_client_name"] = "default"

        conn = _make_pipeline_conn()
        steps = build_setup_steps(ctx)

        for step in steps:
            if step.name in _PANEL_STEPS:
                if "panel" not in ctx:
                    ctx["panel"] = _make_panel_mock()
            if step.name == "Configure panel":
                (tmp_path / "creds").mkdir(parents=True, exist_ok=True)

            try:
                result = step.run(conn, ctx)
            except (KeyError, AttributeError) as e:
                raise AssertionError(f"Step '{step.name}' crashed: {e}. Context keys: {list(ctx._state.keys())}") from e

            if result.status == "failed" and "credentials" not in ctx:
                ctx["credentials"] = make_credentials()
                ctx["panel_configured"] = True


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


class TestContextKeyConsistency:
    """Verify that all ctx keys consumed by steps are documented and produced."""

    # Keys that ConfigurePanel MUST set for downstream steps
    PANEL_PRODUCES = {
        "credentials",
        "panel_configured",
        "panel_username",
        "panel_password",
        "web_base_path",
        "info_page_path",
        "ws_path",
        "xhttp_path",
        "reality_uuid",
        "reality_private_key",
        "reality_public_key",
        "reality_short_id",
        "wss_uuid",
        "xray_bin",
    }

    # Keys that LoginToPanel MUST set
    LOGIN_PRODUCES = {"panel"}

    def test_configure_panel_sets_all_required_keys(self, tmp_path: Path) -> None:
        """ConfigurePanel must populate all keys that downstream steps need."""
        from meridian.provision.panel import ConfigurePanel

        ctx = ProvisionContext(
            ip="198.51.100.1",
            hosted_page=True,
            creds_dir=str(tmp_path / "creds"),
        )
        ctx.panel_port = 2053
        (tmp_path / "creds").mkdir(parents=True, exist_ok=True)

        conn = MockConnection()
        conn.when("x25519", stdout="Private key: testprivatekey12345678\nPublic key: testpublickey123456789\n")
        conn.when("uuid", stdout="test-uuid-1234-5678-9012\n")
        conn.when("xray-linux", stdout="/app/bin/xray-linux-amd64\n")
        conn.when("docker restart", rc=0)
        conn.when("curl -s -o /dev/null -w", stdout="200")

        step = ConfigurePanel(
            creds_path=tmp_path / "creds" / "proxy.yml",
            server_ip="198.51.100.1",
            domain="",
            sni="www.cloudflare.com",
            first_client_name="default",
            panel_port=2053,
            xhttp_enabled=True,
        )
        result = step.run(conn, ctx)

        if result.status != "failed":
            missing = self.PANEL_PRODUCES - set(ctx._state.keys())
            assert not missing, (
                f"ConfigurePanel didn't set these required keys: {missing}. Keys set: {set(ctx._state.keys())}"
            )

    def test_nginx_resolves_paths_from_context(self, tmp_path: Path) -> None:
        """ConfigureNginx with empty constructor args resolves from ctx."""
        from meridian.provision.services import ConfigureNginx

        ctx = ProvisionContext(
            ip="198.51.100.1",
            hosted_page=True,
            xhttp_enabled=True,
            creds_dir=str(tmp_path),
        )
        ctx.panel_port = 2053
        ctx.xhttp_port = 31000
        # Simulate what ConfigurePanel would have set
        ctx["web_base_path"] = "panelpath"
        ctx["info_page_path"] = "infopath"
        ctx["xhttp_path"] = "xhttppath"
        ctx["ws_path"] = "wspath"

        conn = MockConnection()
        conn.when("systemctl", rc=0)
        conn.when("nginx -t", rc=0)

        # ConfigureNginx is created with empty paths — must resolve from ctx
        step = ConfigureNginx(domain="")

        try:
            step.run(conn, ctx)
        except KeyError as e:
            raise AssertionError(
                f"ConfigureNginx couldn't resolve '{e}' from context. ConfigurePanel must set this key."
            ) from e

        # Verify the resolved paths were used in the nginx config
        nginx_calls = [c for c in conn.calls if "nginx" in c or "printf" in c]
        config_text = " ".join(nginx_calls)
        assert "xhttppath" in config_text or any("xhttppath" in c for c in conn.calls), (
            "xhttp_path from context wasn't used in nginx config"
        )
