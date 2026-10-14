"""Tests for nginx and connection page provisioning steps."""

from __future__ import annotations

import time

import pytest

from meridian.provision.nginx import (
    ConfigureNginx,
    DeployPWAAssets,
    InstallNginx,
)
from meridian.provision.nginx_render import (
    render_nginx_http_config,
    render_nginx_ip_config,
    render_nginx_stream_config,
)
from meridian.provision.steps import ProvisionContext
from meridian.provision.tls import IssueTLSCert
from tests.support.mock_connection import MockConnection

_STREAM = {
    "reality_sni": "www.microsoft.com",
    "reality_backend_port": 10443,
    "nginx_internal_port": 8443,
    "server_ip": "198.51.100.1",
}
_IP = {
    "server_ip": "198.51.100.1",
    "nginx_internal_port": 8443,
    "panel_web_base_path": "secretpanel",
    "panel_internal_port": 2053,
    "info_page_path": "connect",
}
_DOMAIN = {
    "domain": "example.com",
    "nginx_internal_port": 8443,
    "ws_path": "wspath",
    "wss_internal_port": 28000,
    "panel_web_base_path": "secretpanel",
    "panel_internal_port": 2053,
    "info_page_path": "connect",
}

# The legacy stream file only runs until a V4 listener owns :443, so it gets one row each.
CONFIGS = {
    "stream": render_nginx_stream_config(**_STREAM),
    "stream_domain": render_nginx_stream_config(**_STREAM, domain="example.com"),
    "ip": render_nginx_ip_config(**_IP),
    "ip_xhttp": render_nginx_ip_config(**_IP, xhttp_path="xh-abc123", xhttp_internal_port=29000),
    "domain": render_nginx_http_config(**_DOMAIN),
    "domain_xhttp": render_nginx_http_config(**_DOMAIN, xhttp_path="xh-def456", xhttp_internal_port=29500),
}

_SECURITY_HEADERS = ("X-Frame-Options", "X-Content-Type-Options", "Referrer-Policy")
_PWA_CACHE = ("$meridian_cache", "max-age=86400", "$meridian_sw", "Service-Worker-Allowed", "stats/")
# Connection pages need alias: it strips the location prefix.
_CONNECTION_PAGES = ("location /connect/", "alias /var/www/private/")
# Missing h2 ALPN is a fingerprinting vector.
_HTTP2 = ("ssl;", "http2 on;", "server_tokens off")
_NGINX_ERRORS = ("location = /", "return 403", "return 404")

PRESENT = {
    "stream": (
        "ssl_preread on",
        "proxy_pass $meridian_backend",
        "www.microsoft.com  xray_reality",
        "127.0.0.1:10443",
        "198.51.100.1  nginx_https",
        # Browsers connecting to a bare IP send no SNI (RFC 6066).
        '""  nginx_https',
        # Unknown SNIs are TCP-proxied to the Reality dest: no SNI routing differential.
        "default  reality_dest",
        "upstream reality_dest",
        "www.microsoft.com:443",
        "listen 443;",
        "listen [::]:443;",
        "proxy_connect_timeout 5s",
        "proxy_timeout 30m",
        "proxy_socket_keepalive on",
    ),
    "stream_domain": ("example.com  nginx_https", "198.51.100.1  nginx_https"),
    "ip": (
        "ssl_certificate",
        "/etc/ssl/meridian/fullchain.pem",
        "/.well-known/acme-challenge/",
        "no-cache, must-revalidate",
        "TLSv1.2 TLSv1.3",
        "default-src 'self'",
        *_SECURITY_HEADERS,
        *_PWA_CACHE,
        *_CONNECTION_PAGES,
        *_HTTP2,
        *_NGINX_ERRORS,
    ),
    "domain": (
        "proxy_set_header Upgrade",
        "proxy_set_header Connection $connection_upgrade",
        "proxy_pass http://127.0.0.1:28000",
        # Domain mode has real content, so port 80 redirects to HTTPS.
        "return 301",
        "$host$request_uri",
        *_SECURITY_HEADERS,
        *_PWA_CACHE,
        *_CONNECTION_PAGES,
        *_HTTP2,
        "return 403",
        "return 404",
    ),
    "ip_xhttp": (
        "upstream meridian_xhttp",
        "127.0.0.1:29000",
        "keepalive 32",
        # Keepalive to Xray needs the Connection header cleared.
        'proxy_set_header Connection ""',
        "location = /xh-abc123 {",
        "location /xh-abc123/ {",
        "proxy_buffering off",
        "proxy_request_buffering off",
        "proxy_read_timeout 86400s",
        "proxy_send_timeout 86400s",
    ),
    "domain_xhttp": (
        "upstream meridian_xhttp",
        "127.0.0.1:29500",
        "keepalive 32",
        "proxy_pass http://meridian_xhttp",
        "proxy_buffering off",
        "xh-def456",
    ),
}

ABSENT = {
    "stream": ("example.com", "blackhole"),
    # IP mode never redirects port 80 to HTTPS: a redirect to a 403 is a contradiction.
    "ip": ("XHTTP", "meridian_xhttp", "VLESS+WSS", "TLSv1.0", "TLSv1.1", "SSLv", "return 301"),
}


def _cases(table: dict[str, tuple[str, ...]]) -> list:
    return [pytest.param(cfg, needle, id=f"{cfg}:{needle}") for cfg, needles in table.items() for needle in needles]


@pytest.mark.parametrize(
    ("config", "needle"),
    _cases(PRESENT),
)
def test_rendered_config_contains(config: str, needle: str) -> None:
    assert needle in CONFIGS[config]


@pytest.mark.parametrize(
    ("config", "needle"),
    _cases(ABSENT),
)
def test_rendered_config_omits(config: str, needle: str) -> None:
    assert needle not in CONFIGS[config]


def test_stream_without_domain_routes_only_server_ip_and_no_sni_to_nginx() -> None:
    nginx_rules = [
        line
        for line in CONFIGS["stream"].splitlines()
        if "nginx_https" in line and "upstream" not in line and "server" not in line
    ]
    assert len(nginx_rules) == 2


@pytest.mark.parametrize("config", ["ip", "domain"])
def test_https_block_serves_only_nginx_generated_errors(config: str) -> None:
    """Custom HTML or a silent 444 after TLS would fingerprint every Meridian server."""
    https_block = CONFIGS[config].split("listen 80")[0]
    assert "return 403" in https_block
    assert "return 404" in https_block
    assert "return 444" not in https_block
    assert "return 200" not in https_block
    assert "default_type text/html" not in https_block


@pytest.mark.parametrize("config", ["ip", "domain"])
def test_panel_proxy_passes_websocket_upgrade_for_the_remnawave_ui(config: str) -> None:
    cfg = CONFIGS[config]
    panel_block = cfg[cfg.index("secretpanel") :]
    assert "proxy_set_header Upgrade" in panel_block
    assert "proxy_set_header Connection $connection_upgrade" in panel_block
    assert "proxy_set_header Host $host" in panel_block
    assert "proxy_http_version 1.1" in panel_block


def test_panel_subpath_rewrites_match_the_3_4_frontend_bundle() -> None:
    cfg = CONFIGS["ip"]
    # The 3.4 bundle emits route/asset literals in backticks; quoted forms never match.
    for literal in ("dashboard", "auth", "oauth2/", "locales/", "lotties/", "favicons/", "assets/"):
        assert f"sub_filter '`/{literal}' '`/secretpanel/{literal}';" in cfg
    assert "sub_filter '\"/" not in cfg
    # /api calls ride the rewritten axios baseURL; rewriting them would double the prefix.
    assert "'`/api" not in cfg
    assert "proxy_redirect /api/ /secretpanel/api/;" in cfg
    assert "proxy_cookie_path /api/ /secretpanel/api/;" in cfg


def test_ip_mode_port_80_only_serves_acme_challenges() -> None:
    cfg = CONFIGS["ip"]
    assert "return 403" in cfg.split("listen 80")[1]
    assert cfg.count("server_tokens off") == 2


def test_internal_https_server_redirects_relatively_so_browsers_stay_on_443() -> None:
    """`/<panel-path>` without a slash redirected to https://IP:8443/... before."""
    cfg = CONFIGS["ip"]
    https_block = cfg[cfg.index("listen 127.0.0.1:8443 ssl;") :]
    assert "absolute_redirect off;" in https_block
    # IP-mode XHTTP joins this no-SNI server through V4 location files.
    assert "include /etc/nginx/meridian-v4-locations/*.conf;" in https_block


def test_xhttp_routes_both_path_forms_before_the_panel() -> None:
    cfg = CONFIGS["ip_xhttp"]
    assert cfg.count("proxy_pass http://meridian_xhttp;") == 2
    assert cfg.index("xh-abc123") < cfg.index("secretpanel")


def _installed_nginx(*, cron_rc: int = 0, cron_install_rc: int = 0) -> MockConnection:
    conn = MockConnection()
    conn.when("dpkg -l nginx", stdout="ii  nginx")
    conn.when("nginx -V", stdout="--with-stream_ssl_preread_module")
    conn.when("test -f /root/.acme.sh/acme.sh", rc=0)
    conn.when("crontab -l", rc=cron_rc)
    conn.when("acme.sh --install-cronjob", stderr="crontab missing" if cron_install_rc else "", rc=cron_install_rc)
    return conn


class TestInstallNginx:
    def test_already_installed_returns_ok(self):
        conn = _installed_nginx()
        result = InstallNginx().run(conn, ProvisionContext(ip="198.51.100.1"))
        assert result.status == "ok"
        conn.assert_called_with_pattern("acme.sh --install-cronjob")

    def test_distro_nginx_older_than_1_25_is_replaced_from_nginx_org(self):
        # Ubuntu 24.04 ships nginx 1.24, which rejects the rendered `http2 on;`.
        conn = MockConnection()
        conn.when("dpkg -l nginx", stdout="ii  nginx")
        conn.when("nginx -v", stdout="nginx version: nginx/1.24.0 (Ubuntu)")
        conn.when("nginx -V", stdout="--with-stream_ssl_preread_module")
        conn.when("ID", stdout="ubuntu")
        conn.when("VERSION_CODENAME", stdout="noble")
        conn.when("test -f /root/.acme.sh/acme.sh", stdout="", rc=0)
        conn.when("crontab -l", stdout="acme.sh --cron", rc=0)

        InstallNginx().run(conn, ProvisionContext(ip="198.51.100.1"))

        conn.assert_called_with_pattern("nginx.org/keys/nginx_signing.key")

    def test_acme_sh_is_pinned_and_verified_before_it_runs(self):
        from meridian.config import ACME_SH_SHA256, ACME_SH_VERSION

        conn = MockConnection()
        conn.when("dpkg -l nginx", stdout="ii  nginx")
        conn.when("nginx -V", stdout="--with-stream_ssl_preread_module")
        conn.when("test -f /root/.acme.sh/acme.sh", rc=1)
        conn.when("systemctl", stdout="")

        InstallNginx(email="ops@example.com").run(conn, ProvisionContext(ip="198.51.100.1"))

        install = next(call for call in conn.calls if "acme.sh --install --home" in call)
        assert f"acmesh-official/acme.sh/{ACME_SH_VERSION}/acme.sh" in install
        assert ACME_SH_SHA256 in install and "sha256sum -c" in install
        assert install.index("sha256sum -c") < install.index("sh ./acme.sh --install")
        assert "| sh -" not in install and "| sh\n" not in install and "get.acme.sh" not in install
        assert "--accountemail ops@example.com" in install

    def test_missing_acme_cron_returns_changed(self):
        conn = _installed_nginx(cron_rc=1)
        assert InstallNginx().run(conn, ProvisionContext(ip="198.51.100.1")).status == "changed"

    def test_acme_cron_install_failure_returns_failed(self):
        conn = _installed_nginx(cron_rc=1, cron_install_rc=1)
        result = InstallNginx().run(conn, ProvisionContext(ip="198.51.100.1"))
        assert result.status == "failed"
        assert "cron job" in result.detail


def _nginx_conn(*, cert_exists: bool) -> MockConnection:
    conn = MockConnection()
    conn.when("dig +short", stdout="198.51.100.1")
    conn.when("test -f /etc/ssl/meridian/fullchain.pem", rc=0 if cert_exists else 1)
    conn.when("grep", stdout="stream {", rc=0)
    conn.when("nginx -t", stdout="syntax is ok")
    return conn


class TestConfigureNginx:
    def test_deploys_config(self):
        conn = _nginx_conn(cert_exists=True)
        step = ConfigureNginx(domain="", ip_mode=True, server_ip="198.51.100.1")
        result = step.run(conn, ProvisionContext(ip="198.51.100.1"))
        assert result.status == "changed"
        assert "nginx" in result.detail

    @pytest.mark.parametrize(
        ("domain", "san"),
        [("", "subjectAltName=IP:198.51.100.1"), ("example.com", "subjectAltName=DNS:example.com")],
    )
    def test_bootstrap_cert_subject_alt_name_follows_mode(self, domain, san):
        conn = _nginx_conn(cert_exists=False)
        ctx = ProvisionContext(ip="198.51.100.1", domain=domain)
        step = ConfigureNginx(domain=domain, ip_mode=not domain, server_ip="198.51.100.1")

        assert step.run(conn, ctx).status == "changed"
        bootstrap_calls = [c for c in conn.calls if "openssl req -x509" in c]
        assert bootstrap_calls
        assert san in bootstrap_calls[0]

    @staticmethod
    def _rerun(*, v4_owns_443: bool) -> MockConnection:
        conn = MockConnection()
        conn.when("meridian-v4-*.conf", rc=0 if v4_owns_443 else 2)
        conn.when("test -f /etc/ssl/meridian/fullchain.pem", stdout="", rc=0)
        conn.when("grep", stdout="stream {", rc=0)
        conn.when("nginx -t", stdout="syntax is ok")
        ctx = ProvisionContext(ip="198.51.100.1")
        result = ConfigureNginx(domain="", ip_mode=True, server_ip="198.51.100.1").run(conn, ctx)
        assert result.status == "changed"
        return conn

    def test_writes_legacy_stream_config_on_a_fresh_server(self):
        assert "/etc/nginx/stream.d/meridian.conf" in self._rerun(v4_owns_443=False).write_map

    def test_rerun_never_duplicates_a_v4_owned_443_listener(self):
        conn = self._rerun(v4_owns_443=True)

        assert "/etc/nginx/stream.d/meridian.conf" not in conn.write_map
        assert "/etc/nginx/conf.d/meridian-http.conf" in conn.write_map


def _acme(*, info: str = "", info_rc: int = 1, issue_rc: int = 2) -> MockConnection:
    """acme.sh exits 2 from --issue when the certificate is still valid."""
    conn = MockConnection()
    conn.when("acme.sh --info", stdout=info, rc=info_rc)
    conn.when("acme.sh --issue", rc=issue_rc)
    return conn


def _issue(conn: MockConnection, *, domain: str = "") -> tuple[str, list[str]]:
    if domain:
        step = IssueTLSCert(domain=domain, ip_mode=False)
    else:
        step = IssueTLSCert(domain="", ip_mode=True, server_ip="198.51.100.1")
    result = step.run(conn, ProvisionContext(ip="198.51.100.1"))
    assert result.status == "changed"
    issue_calls = [c for c in conn.calls if "acme.sh --issue" in c]
    assert issue_calls
    return result.detail, issue_calls


class TestIssueTLSCert:
    def test_ip_cert_is_shortlived_with_an_explicit_renew_window(self):
        conn = _acme()
        detail, issue_calls = _issue(conn)
        assert "TLS cert issued" in detail
        assert "shortlived" in issue_calls[0]
        assert "--days 5" in issue_calls[0]
        assert "--force" not in issue_calls[0]
        conn.assert_called_with_pattern("acme.sh --install-cert")

    def test_acme_failure_blocks_panel_credential_exchange(self):
        conn = _acme(issue_rc=1)
        result = IssueTLSCert(domain="", ip_mode=True, server_ip="198.51.100.1").run(
            conn, ProvisionContext(ip="198.51.100.1")
        )
        assert result.status == "failed"
        assert "trusted TLS certificate" in result.detail
        assert "not sent over self-signed TLS" in result.detail
        conn.assert_not_called_with_pattern("systemctl reload")

    def test_install_cert_failure_returns_failed(self):
        conn = _acme()
        conn.when("acme.sh --install-cert", stderr="copy failed", rc=1)
        result = IssueTLSCert(domain="", ip_mode=True, server_ip="198.51.100.1").run(
            conn, ProvisionContext(ip="198.51.100.1")
        )
        assert result.status == "failed"
        assert "Failed to install TLS cert" in result.detail
        assert all(c != "systemctl reload nginx" for c in conn.calls)

    def test_reload_failure_returns_failed(self):
        conn = _acme()
        conn.when("acme.sh --install-cert", rc=0)  # its --reloadcmd also names the reload
        conn.when("systemctl reload nginx", stderr="reload failed", rc=1)
        result = IssueTLSCert(domain="", ip_mode=True, server_ip="198.51.100.1").run(
            conn, ProvisionContext(ip="198.51.100.1")
        )
        assert result.status == "failed"
        assert "Failed to reload nginx" in result.detail

    def test_domain_mode_omits_shortlived(self):
        _, issue_calls = _issue(_acme(), domain="example.com")
        assert "shortlived" not in issue_calls[0]
        assert "--days" not in issue_calls[0]

    def test_uses_configured_acme_server(self, monkeypatch):
        monkeypatch.setattr("meridian.provision.tls.ACME_SERVER", "https://acme.test/directory")
        _, issue_calls = _issue(_acme())
        assert "https://acme.test/directory" in issue_calls[0]

    @pytest.mark.parametrize(
        ("info", "issue_rc", "forced"),
        [
            pytest.param("Le_RenewalDays='30'\n", 0, True, id="stale-renewal-days"),
            pytest.param(f"Le_NextRenewTime='{int(time.time()) + 30 * 86400}'\n", 0, True, id="far-next-renew"),
            pytest.param(f"Le_NextRenewTime='{int(time.time()) + 86400}'\n", 2, False, id="short-next-renew"),
            pytest.param("Le_RenewalDays='5'\n", 2, False, id="correct-schedule"),
        ],
    )
    def test_ip_mode_forces_renewal_only_for_a_stale_acme_schedule(self, info, issue_rc, forced):
        conn = _acme(info=f"Le_Domain='198.51.100.1'\n{info}", info_rc=0, issue_rc=issue_rc)
        _, issue_calls = _issue(conn)
        assert "shortlived" in issue_calls[0]
        assert ("--force" in issue_calls[0]) is forced


class TestDeployPWAAssets:
    def test_uploads_static_files(self):
        result = DeployPWAAssets().run(MockConnection(), ProvisionContext(ip="198.51.100.1"))
        assert result.status == "changed"
        assert "PWA" in result.detail

    def test_failure_returns_failed(self):
        conn = MockConnection()
        conn.when("cat >", rc=1)
        assert DeployPWAAssets().run(conn, ProvisionContext(ip="198.51.100.1")).status == "failed"
