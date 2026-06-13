"""Tests for nginx and connection page provisioning steps."""

from __future__ import annotations

import time
from pathlib import Path

from meridian.provision.nginx import (
    ConfigureNginx,
    DeployPWAAssets,
    InstallNginx,
    _render_nginx_http_config,
    _render_nginx_ip_config,
    _render_nginx_stream_config,
)
from meridian.provision.steps import ProvisionContext
from meridian.provision.tls import IssueTLSCert
from tests.support.mock_connection import MockConnection

# ---------------------------------------------------------------------------
# Config rendering: nginx stream (SNI routing)
# ---------------------------------------------------------------------------


class TestRenderNginxStreamConfig:
    def test_contains_sni_routing(self):
        cfg = _render_nginx_stream_config(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
            server_ip="198.51.100.1",
        )
        assert "www.microsoft.com" in cfg
        assert "ssl_preread on" in cfg
        assert "proxy_pass $meridian_backend" in cfg

    def test_reality_sni_routes_to_xray(self):
        cfg = _render_nginx_stream_config(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
            server_ip="198.51.100.1",
        )
        assert "www.microsoft.com  xray_reality" in cfg
        assert "127.0.0.1:10443" in cfg

    def test_server_ip_routes_to_nginx(self):
        cfg = _render_nginx_stream_config(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
            server_ip="198.51.100.1",
        )
        assert "198.51.100.1  nginx_https" in cfg

    def test_domain_routes_to_nginx(self):
        cfg = _render_nginx_stream_config(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
            server_ip="198.51.100.1",
            domain="example.com",
        )
        assert "example.com  nginx_https" in cfg
        assert "198.51.100.1  nginx_https" in cfg

    def test_no_sni_routes_to_nginx(self):
        """Browsers connecting to bare IP send no SNI (RFC 6066)."""
        cfg = _render_nginx_stream_config(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
            server_ip="198.51.100.1",
        )
        assert '""  nginx_https' in cfg

    def test_unknown_sni_routes_to_reality_dest(self):
        """Unknown SNI routes to reality dest — eliminates SNI differential."""
        cfg = _render_nginx_stream_config(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
            server_ip="198.51.100.1",
        )
        assert "default  reality_dest" in cfg
        assert "upstream reality_dest" in cfg
        assert "www.microsoft.com:443" in cfg

    def test_no_domain_no_domain_rule(self):
        """Without domain, only server IP + no-SNI route to nginx. Default → reality_dest."""
        cfg = _render_nginx_stream_config(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
            server_ip="198.51.100.1",
        )
        assert "example.com" not in cfg
        # Count nginx_https map entries: server IP + no-SNI = 2
        # (default goes to reality_dest, not nginx_https)
        nginx_rules = [
            line
            for line in cfg.splitlines()
            if "nginx_https" in line and "upstream" not in line and "server" not in line
        ]
        assert len(nginx_rules) == 2


# ---------------------------------------------------------------------------
# Config rendering: nginx http (IP mode)
# ---------------------------------------------------------------------------


class TestRenderNginxIpConfig:
    def test_has_ssl_certificate(self):
        cfg = _render_nginx_ip_config(
            server_ip="198.51.100.1",
            nginx_internal_port=8443,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
        )
        assert "ssl_certificate" in cfg
        assert "/etc/ssl/meridian/fullchain.pem" in cfg

    def test_has_server_tokens_off(self):
        cfg = _render_nginx_ip_config(
            server_ip="198.51.100.1",
            nginx_internal_port=8443,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
        )
        assert "server_tokens off" in cfg

    def test_has_acme_challenge_location(self):
        cfg = _render_nginx_ip_config(
            server_ip="198.51.100.1",
            nginx_internal_port=8443,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
        )
        assert "/.well-known/acme-challenge/" in cfg


# ---------------------------------------------------------------------------
# nginx config: PWA headers (cache control maps)
# ---------------------------------------------------------------------------


class TestNginxPWAHeaders:
    def _ip_config(self) -> str:
        return _render_nginx_ip_config(
            server_ip="198.51.100.1",
            nginx_internal_port=8443,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
        )

    def _domain_config(self) -> str:
        return _render_nginx_http_config(
            domain="example.com",
            nginx_internal_port=8443,
            ws_path="wspath",
            wss_internal_port=28000,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
        )

    def test_ip_config_has_cache_map(self):
        cfg = self._ip_config()
        assert "$meridian_cache" in cfg
        assert "max-age=86400" in cfg

    def test_ip_config_has_sw_header_map(self):
        cfg = self._ip_config()
        assert "$meridian_sw" in cfg
        assert "Service-Worker-Allowed" in cfg

    def test_ip_config_has_dynamic_no_cache(self):
        cfg = self._ip_config()
        assert "no-cache, must-revalidate" in cfg

    def test_domain_config_has_cache_map(self):
        cfg = self._domain_config()
        assert "$meridian_cache" in cfg
        assert "max-age=86400" in cfg

    def test_domain_config_has_sw_header_map(self):
        cfg = self._domain_config()
        assert "$meridian_sw" in cfg
        assert "Service-Worker-Allowed" in cfg


# ---------------------------------------------------------------------------
# nginx config: XHTTP block
# ---------------------------------------------------------------------------


class TestNginxXHTTPBlock:
    def test_ip_config_xhttp_proxy_pass(self):
        cfg = _render_nginx_ip_config(
            server_ip="198.51.100.1",
            nginx_internal_port=8443,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
            xhttp_path="xh-abc123",
            xhttp_internal_port=29000,
        )
        assert "proxy_pass http://meridian_xhttp" in cfg
        assert "proxy_buffering off" in cfg
        assert "xh-abc123" in cfg

    def test_domain_config_xhttp_proxy_pass(self):
        cfg = _render_nginx_http_config(
            domain="example.com",
            nginx_internal_port=8443,
            ws_path="wspath",
            wss_internal_port=28000,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
            xhttp_path="xh-def456",
            xhttp_internal_port=29500,
        )
        assert "proxy_pass http://meridian_xhttp" in cfg
        assert "proxy_buffering off" in cfg
        assert "xh-def456" in cfg

    def test_xhttp_routes_exact_and_slash_paths(self):
        cfg = _render_nginx_ip_config(
            server_ip="198.51.100.1",
            nginx_internal_port=8443,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
            xhttp_path="xh-exact",
            xhttp_internal_port=29000,
        )
        assert "location = /xh-exact {" in cfg
        assert "location /xh-exact/ {" in cfg
        assert cfg.count("proxy_pass http://meridian_xhttp;") == 2

    def test_xhttp_before_panel(self):
        cfg = _render_nginx_ip_config(
            server_ip="198.51.100.1",
            nginx_internal_port=8443,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
            xhttp_path="xh-test",
            xhttp_internal_port=29000,
        )
        xhttp_pos = cfg.index("xh-test")
        panel_pos = cfg.index("secretpanel")
        assert xhttp_pos < panel_pos

    def test_no_xhttp_without_params(self):
        cfg = _render_nginx_ip_config(
            server_ip="198.51.100.1",
            nginx_internal_port=8443,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
        )
        assert "XHTTP" not in cfg
        assert "meridian_xhttp" not in cfg

    def test_xhttp_has_streaming_timeouts(self):
        cfg = _render_nginx_ip_config(
            server_ip="198.51.100.1",
            nginx_internal_port=8443,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
            xhttp_path="xh-test",
            xhttp_internal_port=29000,
        )
        assert "proxy_read_timeout 86400s" in cfg
        assert "proxy_send_timeout 86400s" in cfg
        assert "proxy_request_buffering off" in cfg

    def test_xhttp_upstream_keepalive(self):
        """XHTTP upstream block enables connection reuse to Xray."""
        cfg = _render_nginx_ip_config(
            server_ip="198.51.100.1",
            nginx_internal_port=8443,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
            xhttp_path="xh-test",
            xhttp_internal_port=29000,
        )
        assert "upstream meridian_xhttp" in cfg
        assert "keepalive 32" in cfg
        assert "127.0.0.1:29000" in cfg
        # Connection header must be cleared for keepalive to work
        assert 'proxy_set_header Connection ""' in cfg

    def test_xhttp_upstream_keepalive_domain(self):
        """Domain mode also gets upstream keepalive."""
        cfg = _render_nginx_http_config(
            domain="example.com",
            nginx_internal_port=8443,
            ws_path="wspath",
            wss_internal_port=28000,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
            xhttp_path="xh-test",
            xhttp_internal_port=29500,
        )
        assert "upstream meridian_xhttp" in cfg
        assert "keepalive 32" in cfg
        assert "127.0.0.1:29500" in cfg


# ---------------------------------------------------------------------------
# nginx config: panel proxy (WebSocket support for Remnawave admin UI)
# ---------------------------------------------------------------------------


class TestNginxPanelProxy:
    def _ip_config(self) -> str:
        return _render_nginx_ip_config(
            server_ip="198.51.100.1",
            nginx_internal_port=8443,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
        )

    def _domain_config(self) -> str:
        return _render_nginx_http_config(
            domain="example.com",
            nginx_internal_port=8443,
            ws_path="wspath",
            wss_internal_port=28000,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
        )

    def test_ip_panel_has_websocket_upgrade(self):
        """Remnawave admin UI uses WebSocket — proxy must pass upgrade headers."""
        cfg = self._ip_config()
        panel_block = cfg[cfg.index("secretpanel") :]
        assert "proxy_set_header Upgrade" in panel_block
        assert "proxy_set_header Connection $connection_upgrade" in panel_block

    def test_domain_panel_has_websocket_upgrade(self):
        cfg = self._domain_config()
        panel_block = cfg[cfg.index("secretpanel") :]
        assert "proxy_set_header Upgrade" in panel_block
        assert "proxy_set_header Connection $connection_upgrade" in panel_block

    def test_panel_has_host_header(self):
        cfg = self._ip_config()
        panel_block = cfg[cfg.index("secretpanel") :]
        assert "proxy_set_header Host $host" in panel_block

    def test_panel_has_http11(self):
        """HTTP/1.1 required for WebSocket upgrade."""
        cfg = self._ip_config()
        panel_block = cfg[cfg.index("secretpanel") :]
        assert "proxy_http_version 1.1" in panel_block


# ---------------------------------------------------------------------------
# nginx config: location structure (connection pages)
# ---------------------------------------------------------------------------


class TestNginxLocationStructure:
    def _ip_config(self) -> str:
        return _render_nginx_ip_config(
            server_ip="198.51.100.1",
            nginx_internal_port=8443,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
        )

    def _domain_config(self) -> str:
        return _render_nginx_http_config(
            domain="example.com",
            nginx_internal_port=8443,
            ws_path="wspath",
            wss_internal_port=28000,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
        )

    def test_ip_uses_alias(self):
        """Connection pages must use alias (strips location prefix)."""
        cfg = self._ip_config()
        assert "location /connect/" in cfg
        assert "alias /var/www/private/" in cfg

    def test_domain_uses_alias(self):
        cfg = self._domain_config()
        assert "location /connect/" in cfg
        assert "alias /var/www/private/" in cfg

    def test_ip_has_security_headers(self):
        cfg = self._ip_config()
        assert "X-Frame-Options" in cfg
        assert "X-Content-Type-Options" in cfg
        assert "Referrer-Policy" in cfg

    def test_domain_has_security_headers(self):
        cfg = self._domain_config()
        assert "X-Frame-Options" in cfg
        assert "X-Content-Type-Options" in cfg
        assert "Referrer-Policy" in cfg

    def test_ip_cache_map_has_stats(self):
        cfg = self._ip_config()
        assert "stats/" in cfg

    def test_domain_cache_map_has_stats(self):
        cfg = self._domain_config()
        assert "stats/" in cfg


# ---------------------------------------------------------------------------
# nginx config: domain mode WSS
# ---------------------------------------------------------------------------


class TestNginxWSS:
    def test_domain_config_has_websocket_upgrade(self):
        cfg = _render_nginx_http_config(
            domain="example.com",
            nginx_internal_port=8443,
            ws_path="wspath",
            wss_internal_port=28000,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
        )
        assert "proxy_set_header Upgrade" in cfg
        assert "proxy_set_header Connection $connection_upgrade" in cfg
        assert "proxy_pass http://127.0.0.1:28000" in cfg

    def test_ip_config_has_no_wss(self):
        """IP mode should not have WSS location block."""
        cfg = _render_nginx_ip_config(
            server_ip="198.51.100.1",
            nginx_internal_port=8443,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
        )
        assert "VLESS+WSS" not in cfg


# ---------------------------------------------------------------------------
# nginx config: decoy mode
# ---------------------------------------------------------------------------


class TestNginxDecoy:
    def test_default_returns_nginx_403_404(self):
        """Default uses nginx's built-in 403/404 — not custom HTML.

        Custom HTML (like a placeholder page) would be fingerprintable:
        a censor finding one Meridian server could scan for the same HTML
        on all IPs.  nginx-generated error pages are identical across all
        nginx installations — no Meridian-specific content.
        """
        cfg = _render_nginx_ip_config(
            server_ip="198.51.100.1",
            nginx_internal_port=8443,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
        )
        assert "return 403" in cfg
        assert "return 404" in cfg
        # No custom HTML content in the HTTPS block
        https_block = cfg.split("listen 80")[0]
        assert "return 444" not in https_block
        assert "return 200" not in https_block

    def test_domain_default_returns_nginx_403_404(self):
        """Domain mode also uses nginx-generated 403/404."""
        cfg = _render_nginx_http_config(
            domain="example.com",
            nginx_internal_port=8443,
            ws_path="wspath",
            wss_internal_port=28000,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
        )
        assert "return 403" in cfg
        assert "return 404" in cfg
        https_block = cfg.split("listen 80")[0]
        assert "return 444" not in https_block
        assert "return 200" not in https_block

    def test_default_has_security_headers(self):
        cfg = _render_nginx_ip_config(
            server_ip="198.51.100.1",
            nginx_internal_port=8443,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
        )
        assert "X-Frame-Options" in cfg
        assert "X-Content-Type-Options" in cfg
        assert "Referrer-Policy" in cfg

    def test_server_tokens_off(self):
        """Both modes should have server_tokens off."""
        for cfg in [
            _render_nginx_ip_config(
                server_ip="198.51.100.1",
                nginx_internal_port=8443,
                panel_web_base_path="secretpanel",
                panel_internal_port=2053,
                info_page_path="connect",
            ),
            _render_nginx_http_config(
                domain="example.com",
                nginx_internal_port=8443,
                ws_path="wspath",
                wss_internal_port=28000,
                panel_web_base_path="secretpanel",
                panel_internal_port=2053,
                info_page_path="connect",
            ),
        ]:
            assert "server_tokens off" in cfg


# ---------------------------------------------------------------------------
# nginx config: fingerprinting resistance
# ---------------------------------------------------------------------------


class TestNginxFingerprinting:
    """Tests for anti-fingerprinting properties discovered during real-server testing."""

    def test_http2_enabled(self):
        """HTTP/2 must be enabled — missing h2 ALPN is a fingerprinting vector."""
        cfg = _render_nginx_ip_config(
            server_ip="198.51.100.1",
            nginx_internal_port=8443,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
        )
        assert "ssl http2" in cfg

    def test_domain_http2_enabled(self):
        cfg = _render_nginx_http_config(
            domain="example.com",
            nginx_internal_port=8443,
            ws_path="wspath",
            wss_internal_port=28000,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
        )
        assert "ssl http2" in cfg

    def test_tls_modern_protocols_only(self):
        """Only TLSv1.2 and TLSv1.3 — no older protocols."""
        cfg = _render_nginx_ip_config(
            server_ip="198.51.100.1",
            nginx_internal_port=8443,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
        )
        assert "TLSv1.2 TLSv1.3" in cfg
        assert "TLSv1.0" not in cfg
        assert "TLSv1.1" not in cfg
        assert "SSLv" not in cfg

    def test_stream_ipv4_and_ipv6_listening(self):
        """Stream server listens on both IPv4 and IPv6."""
        cfg = _render_nginx_stream_config(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
            server_ip="198.51.100.1",
        )
        assert "listen 443;" in cfg
        assert "listen [::]:443;" in cfg

    def test_stream_proxy_connect_timeout(self):
        """Stream proxy should have a short connect timeout (good practice)."""
        cfg = _render_nginx_stream_config(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
            server_ip="198.51.100.1",
        )
        assert "proxy_connect_timeout 1s" in cfg

    def test_stream_proxy_timeout(self):
        """Stream proxy needs a long idle timeout for VPN sessions."""
        cfg = _render_nginx_stream_config(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
            server_ip="198.51.100.1",
        )
        assert "proxy_timeout 30m" in cfg

    def test_stream_socket_keepalive(self):
        """TCP keepalives keep relay→exit connections alive through NATs."""
        cfg = _render_nginx_stream_config(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
            server_ip="198.51.100.1",
        )
        assert "proxy_socket_keepalive on" in cfg

    def test_ip_mode_no_http_redirect(self):
        """IP mode must NOT redirect HTTP→HTTPS (redirect to 403 is a contradiction)."""
        cfg = _render_nginx_ip_config(
            server_ip="198.51.100.1",
            nginx_internal_port=8443,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
        )
        assert "return 301" not in cfg
        # Port 80 should still serve ACME challenges
        assert "/.well-known/acme-challenge/" in cfg
        # Port 80 non-ACME should silently close
        http_block = cfg.split("listen 80")[1] if "listen 80" in cfg else ""
        assert "return 403" in http_block

    def test_domain_mode_keeps_http_redirect(self):
        """Domain mode SHOULD redirect HTTP→HTTPS (has real content)."""
        cfg = _render_nginx_http_config(
            domain="example.com",
            nginx_internal_port=8443,
            ws_path="wspath",
            wss_internal_port=28000,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
        )
        assert "return 301" in cfg
        assert "$host$request_uri" in cfg

    def test_port80_server_tokens_off(self):
        """Port 80 server must also have server_tokens off."""
        cfg = _render_nginx_ip_config(
            server_ip="198.51.100.1",
            nginx_internal_port=8443,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
        )
        # Both server blocks should have server_tokens off
        assert cfg.count("server_tokens off") == 2

    def test_no_444_in_https_server_block(self):
        """HTTPS block must never use 444 — silent close after TLS is the highest signal."""
        for cfg in [
            _render_nginx_ip_config(
                server_ip="198.51.100.1",
                nginx_internal_port=8443,
                panel_web_base_path="secretpanel",
                panel_internal_port=2053,
                info_page_path="connect",
            ),
            _render_nginx_http_config(
                domain="example.com",
                nginx_internal_port=8443,
                ws_path="wspath",
                wss_internal_port=28000,
                panel_web_base_path="secretpanel",
                panel_internal_port=2053,
                info_page_path="connect",
            ),
        ]:
            # Split at port 80 to isolate the HTTPS block
            https_block = cfg.split("listen 80")[0]
            assert "return 403" in https_block
            assert "return 404" in https_block
            assert "return 444" not in https_block

    def test_no_custom_html_in_response(self):
        """HTTPS block must not serve custom HTML — it would be fingerprintable."""
        cfg = _render_nginx_ip_config(
            server_ip="198.51.100.1",
            nginx_internal_port=8443,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
        )
        https_block = cfg.split("listen 80")[0]
        assert "return 200" not in https_block
        assert "default_type text/html" not in https_block

    def test_csp_restricts_external_resources(self):
        """CSP must block external resource loading (self-hosted everything)."""
        cfg = _render_nginx_ip_config(
            server_ip="198.51.100.1",
            nginx_internal_port=8443,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
        )
        assert "default-src 'self'" in cfg

    def test_unknown_sni_proxied_to_dest(self):
        """Unknown SNIs must be TCP-proxied to Reality dest, not served by nginx."""
        cfg = _render_nginx_stream_config(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
            server_ip="198.51.100.1",
        )
        assert "default  reality_dest" in cfg
        assert "blackhole" not in cfg

    def test_root_403_vs_default_404(self):
        """Root returns 403, other paths 404 — all nginx-generated, not Meridian."""
        cfg = _render_nginx_ip_config(
            server_ip="198.51.100.1",
            nginx_internal_port=8443,
            panel_web_base_path="secretpanel",
            panel_internal_port=2053,
            info_page_path="connect",
        )
        assert "location = /" in cfg
        assert "return 403" in cfg
        assert "return 404" in cfg


# ---------------------------------------------------------------------------
# InstallNginx step
# ---------------------------------------------------------------------------


class TestInstallNginx:
    def test_already_installed_returns_ok(self, tmp_path: Path):
        conn = MockConnection()
        # nginx installed, stream module available
        conn.when("dpkg -l nginx", stdout="ii  nginx")
        conn.when("nginx -V", stdout="--with-stream_ssl_preread_module")
        # acme.sh installed
        conn.when("test -f /root/.acme.sh/acme.sh", stdout="", rc=0)
        conn.when("crontab -l", stdout="", rc=0)
        conn.when("acme.sh --install-cronjob", stdout="")
        # All other commands succeed
        conn.when("mkdir", stdout="")
        conn.when("chown", stdout="")
        conn.when("rm -f", stdout="")
        # Stop old services
        conn.when("systemctl stop haproxy", stdout="")
        conn.when("systemctl stop caddy", stdout="")
        conn.when("systemctl", stdout="")

        ctx = ProvisionContext(ip="198.51.100.1", creds_dir=str(tmp_path))
        step = InstallNginx()
        result = step.run(conn, ctx)
        assert result.status == "ok"
        conn.assert_called_with_pattern("acme.sh --install-cronjob")

    def test_missing_acme_cron_returns_changed(self, tmp_path: Path):
        conn = MockConnection()
        conn.when("dpkg -l nginx", stdout="ii  nginx")
        conn.when("nginx -V", stdout="--with-stream_ssl_preread_module")
        conn.when("test -f /root/.acme.sh/acme.sh", stdout="", rc=0)
        conn.when("crontab -l", stdout="", rc=1)
        conn.when("acme.sh --install-cronjob", stdout="")
        conn.when("mkdir", stdout="")
        conn.when("chown", stdout="")
        conn.when("rm -f", stdout="")
        conn.when("systemctl stop haproxy", stdout="")
        conn.when("systemctl stop caddy", stdout="")
        conn.when("systemctl", stdout="")

        ctx = ProvisionContext(ip="198.51.100.1", creds_dir=str(tmp_path))
        step = InstallNginx()
        result = step.run(conn, ctx)
        assert result.status == "changed"

    def test_acme_cron_install_failure_returns_failed(self, tmp_path: Path):
        conn = MockConnection()
        conn.when("dpkg -l nginx", stdout="ii  nginx")
        conn.when("nginx -V", stdout="--with-stream_ssl_preread_module")
        conn.when("test -f /root/.acme.sh/acme.sh", stdout="", rc=0)
        conn.when("crontab -l", stdout="", rc=1)
        conn.when("acme.sh --install-cronjob", stderr="crontab missing", rc=1)
        conn.when("mkdir", stdout="")
        conn.when("chown", stdout="")
        conn.when("rm -f", stdout="")
        conn.when("systemctl stop haproxy", stdout="")
        conn.when("systemctl stop caddy", stdout="")
        conn.when("systemctl", stdout="")

        ctx = ProvisionContext(ip="198.51.100.1", creds_dir=str(tmp_path))
        step = InstallNginx()
        result = step.run(conn, ctx)
        assert result.status == "failed"
        assert "cron job" in result.detail


# ---------------------------------------------------------------------------
# ConfigureNginx step
# ---------------------------------------------------------------------------


class TestConfigureNginx:
    def test_deploys_config(self, tmp_path: Path):
        conn = MockConnection()
        # cert exists
        conn.when("test -f /etc/ssl/meridian/fullchain.pem", stdout="", rc=0)
        # All other commands succeed
        conn.when("printf", stdout="")
        conn.when("grep", stdout="stream {", rc=0)
        conn.when("rm -f", stdout="")
        conn.when("nginx -t", stdout="syntax is ok")
        conn.when("systemctl", stdout="")
        conn.when("mkdir", stdout="")

        ctx = ProvisionContext(ip="198.51.100.1", creds_dir=str(tmp_path))
        step = ConfigureNginx(domain="", ip_mode=True, server_ip="198.51.100.1")
        result = step.run(conn, ctx)
        assert result.status == "changed"
        assert "nginx" in result.detail

    def test_bootstrap_cert_uses_ip_subject_alt_name(self, tmp_path: Path):
        conn = MockConnection()
        conn.when("test -f /etc/ssl/meridian/fullchain.pem", stdout="", rc=1)
        conn.when("openssl req -x509", stdout="")
        conn.when("printf", stdout="")
        conn.when("grep", stdout="stream {", rc=0)
        conn.when("rm -f", stdout="")
        conn.when("nginx -t", stdout="syntax is ok")
        conn.when("systemctl", stdout="")
        conn.when("mkdir", stdout="")

        ctx = ProvisionContext(ip="198.51.100.1", creds_dir=str(tmp_path))
        step = ConfigureNginx(domain="", ip_mode=True, server_ip="198.51.100.1")
        result = step.run(conn, ctx)

        assert result.status == "changed"
        bootstrap_calls = [c for c in conn.calls if "openssl req -x509" in c]
        assert bootstrap_calls
        assert "subjectAltName=IP:198.51.100.1" in bootstrap_calls[0]

    def test_bootstrap_cert_uses_dns_subject_alt_name(self, tmp_path: Path):
        conn = MockConnection()
        conn.when("dig +short", stdout="198.51.100.1")
        conn.when("test -f /etc/ssl/meridian/fullchain.pem", stdout="", rc=1)
        conn.when("openssl req -x509", stdout="")
        conn.when("printf", stdout="")
        conn.when("grep", stdout="stream {", rc=0)
        conn.when("rm -f", stdout="")
        conn.when("nginx -t", stdout="syntax is ok")
        conn.when("systemctl", stdout="")
        conn.when("mkdir", stdout="")

        ctx = ProvisionContext(ip="198.51.100.1", domain="example.com", creds_dir=str(tmp_path))
        step = ConfigureNginx(domain="example.com", ip_mode=False, server_ip="198.51.100.1")
        result = step.run(conn, ctx)

        assert result.status == "changed"
        bootstrap_calls = [c for c in conn.calls if "openssl req -x509" in c]
        assert bootstrap_calls
        assert "subjectAltName=DNS:example.com" in bootstrap_calls[0]


# ---------------------------------------------------------------------------
# IssueTLSCert step
# ---------------------------------------------------------------------------


class TestIssueTLSCert:
    def test_cert_already_valid(self, tmp_path: Path):
        conn = MockConnection()
        # acme.sh issue (cert already valid)
        conn.when("acme.sh --info", stdout="", rc=1)
        conn.when("acme.sh --issue", stdout="", rc=2)
        conn.when("acme.sh --install-cert", stdout="")
        conn.when("systemctl", stdout="")

        ctx = ProvisionContext(ip="198.51.100.1", creds_dir=str(tmp_path))
        step = IssueTLSCert(domain="", ip_mode=True, server_ip="198.51.100.1")
        result = step.run(conn, ctx)
        assert result.status == "changed"
        assert "TLS cert issued" in result.detail
        acme_calls = [c for c in conn.calls if "acme.sh --issue" in c]
        assert acme_calls
        assert "shortlived" in acme_calls[0]
        assert "--days 5" in acme_calls[0]
        assert "--force" not in acme_calls[0]
        conn.assert_called_with_pattern("acme.sh --install-cert")

    def test_acme_failure_returns_warning(self, tmp_path: Path):
        """ACME failure returns changed with warning, not failed."""
        conn = MockConnection()
        conn.when("acme.sh --info", stdout="", rc=1)
        conn.when("acme.sh --issue", stdout="", rc=1)

        ctx = ProvisionContext(ip="198.51.100.1", creds_dir=str(tmp_path))
        step = IssueTLSCert(domain="", ip_mode=True, server_ip="198.51.100.1")
        result = step.run(conn, ctx)
        assert result.status == "changed"
        assert "WARNING" in result.detail
        assert "self-signed" in result.detail
        # nginx should NOT be reloaded on ACME failure
        conn.assert_not_called_with_pattern("systemctl reload")

    def test_install_cert_failure_returns_failed(self, tmp_path: Path):
        conn = MockConnection()
        conn.when("acme.sh --info", stdout="", rc=1)
        conn.when("acme.sh --issue", stdout="", rc=2)
        conn.when("acme.sh --install-cert", stderr="copy failed", rc=1)

        ctx = ProvisionContext(ip="198.51.100.1", creds_dir=str(tmp_path))
        step = IssueTLSCert(domain="", ip_mode=True, server_ip="198.51.100.1")
        result = step.run(conn, ctx)
        assert result.status == "failed"
        assert "Failed to install TLS cert" in result.detail
        assert all(c != "systemctl reload nginx" for c in conn.calls)

    def test_reload_failure_returns_failed(self, tmp_path: Path):
        conn = MockConnection()
        conn.when("acme.sh --info", stdout="", rc=1)
        conn.when("acme.sh --issue", stdout="", rc=2)
        conn.when("acme.sh --install-cert", stdout="")
        conn.when("systemctl reload nginx", stderr="reload failed", rc=1)

        ctx = ProvisionContext(ip="198.51.100.1", creds_dir=str(tmp_path))
        step = IssueTLSCert(domain="", ip_mode=True, server_ip="198.51.100.1")
        result = step.run(conn, ctx)
        assert result.status == "failed"
        assert "Failed to reload nginx" in result.detail

    def test_domain_mode_omits_shortlived(self, tmp_path: Path):
        """Domain mode does not use --certificate-profile shortlived."""
        conn = MockConnection()
        conn.when("acme.sh --issue", stdout="", rc=2)
        conn.when("acme.sh --install-cert", stdout="")
        conn.when("systemctl", stdout="")

        ctx = ProvisionContext(ip="198.51.100.1", creds_dir=str(tmp_path))
        step = IssueTLSCert(domain="example.com", ip_mode=False)
        result = step.run(conn, ctx)
        assert result.status == "changed"
        # Verify no shortlived flag in the acme command
        acme_calls = [c for c in conn.calls if "acme.sh --issue" in c]
        assert acme_calls
        assert "shortlived" not in acme_calls[0]
        assert "--days" not in acme_calls[0]

    def test_uses_configured_acme_server(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("meridian.provision.tls.ACME_SERVER", "https://acme.test/directory")
        conn = MockConnection()
        conn.when("acme.sh --info", stdout="", rc=1)
        conn.when("acme.sh --issue", stdout="", rc=2)
        conn.when("acme.sh --install-cert", stdout="")
        conn.when("systemctl", stdout="")

        ctx = ProvisionContext(ip="198.51.100.1", creds_dir=str(tmp_path))
        step = IssueTLSCert(domain="", ip_mode=True, server_ip="198.51.100.1")
        result = step.run(conn, ctx)

        assert result.status == "changed"
        acme_calls = [c for c in conn.calls if "acme.sh --issue" in c]
        assert acme_calls
        assert "https://acme.test/directory" in acme_calls[0]

    def test_ip_mode_force_renews_stale_acme_schedule(self, tmp_path: Path):
        conn = MockConnection()
        conn.when(
            "acme.sh --info",
            stdout="Le_Domain='198.51.100.1'\nLe_RenewalDays='30'\n",
            rc=0,
        )
        conn.when("acme.sh --issue", stdout="", rc=0)
        conn.when("acme.sh --install-cert", stdout="")
        conn.when("systemctl", stdout="")

        ctx = ProvisionContext(ip="198.51.100.1", creds_dir=str(tmp_path))
        step = IssueTLSCert(domain="", ip_mode=True, server_ip="198.51.100.1")
        result = step.run(conn, ctx)
        assert result.status == "changed"
        acme_calls = [c for c in conn.calls if "acme.sh --issue" in c]
        assert acme_calls
        assert "shortlived" in acme_calls[0]
        assert "--days 5" in acme_calls[0]
        assert "--force" in acme_calls[0]

    def test_ip_mode_force_renews_when_renewal_days_missing(self, tmp_path: Path):
        stale_next_renew = int(time.time()) + 30 * 24 * 60 * 60
        conn = MockConnection()
        conn.when(
            "acme.sh --info",
            stdout=f"Le_Domain='198.51.100.1'\nLe_NextRenewTime='{stale_next_renew}'\n",
            rc=0,
        )
        conn.when("acme.sh --issue", stdout="", rc=0)
        conn.when("acme.sh --install-cert", stdout="")
        conn.when("systemctl", stdout="")

        ctx = ProvisionContext(ip="198.51.100.1", creds_dir=str(tmp_path))
        step = IssueTLSCert(domain="", ip_mode=True, server_ip="198.51.100.1")
        result = step.run(conn, ctx)
        assert result.status == "changed"
        acme_calls = [c for c in conn.calls if "acme.sh --issue" in c]
        assert acme_calls
        assert "shortlived" in acme_calls[0]
        assert "--force" in acme_calls[0]

    def test_ip_mode_missing_renewal_days_respects_short_next_renew(self, tmp_path: Path):
        next_renew = int(time.time()) + 24 * 60 * 60
        conn = MockConnection()
        conn.when(
            "acme.sh --info",
            stdout=f"Le_Domain='198.51.100.1'\nLe_NextRenewTime='{next_renew}'\n",
            rc=0,
        )
        conn.when("acme.sh --issue", stdout="", rc=2)
        conn.when("acme.sh --install-cert", stdout="")
        conn.when("systemctl", stdout="")

        ctx = ProvisionContext(ip="198.51.100.1", creds_dir=str(tmp_path))
        step = IssueTLSCert(domain="", ip_mode=True, server_ip="198.51.100.1")
        result = step.run(conn, ctx)
        assert result.status == "changed"
        acme_calls = [c for c in conn.calls if "acme.sh --issue" in c]
        assert acme_calls
        assert "--force" not in acme_calls[0]

    def test_ip_mode_does_not_force_when_schedule_is_already_correct(self, tmp_path: Path):
        conn = MockConnection()
        conn.when(
            "acme.sh --info",
            stdout="Le_Domain='198.51.100.1'\nLe_RenewalDays='5'\n",
            rc=0,
        )
        conn.when("acme.sh --issue", stdout="", rc=2)
        conn.when("acme.sh --install-cert", stdout="")
        conn.when("systemctl", stdout="")

        ctx = ProvisionContext(ip="198.51.100.1", creds_dir=str(tmp_path))
        step = IssueTLSCert(domain="", ip_mode=True, server_ip="198.51.100.1")
        result = step.run(conn, ctx)
        assert result.status == "changed"
        acme_calls = [c for c in conn.calls if "acme.sh --issue" in c]
        assert acme_calls
        assert "shortlived" in acme_calls[0]
        assert "--days 5" in acme_calls[0]
        assert "--force" not in acme_calls[0]


# ---------------------------------------------------------------------------
# DeployPWAAssets step
# ---------------------------------------------------------------------------


class TestDeployPWAAssets:
    def test_uploads_static_files(self, tmp_path: Path):
        conn = MockConnection()
        conn.when("mkdir", stdout="")
        conn.when("cat >", stdout="")

        ctx = ProvisionContext(ip="198.51.100.1", creds_dir=str(tmp_path))
        step = DeployPWAAssets()
        result = step.run(conn, ctx)
        assert result.status == "changed"
        assert "PWA" in result.detail

    def test_failure_returns_failed(self, tmp_path: Path):
        conn = MockConnection()
        conn.when("mkdir", stdout="")
        # File upload fails
        conn.when("cat >", stdout="", rc=1)

        ctx = ProvisionContext(ip="198.51.100.1", creds_dir=str(tmp_path))
        step = DeployPWAAssets()
        result = step.run(conn, ctx)
        assert result.status == "failed"
