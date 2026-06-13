"""Tests for the censor probe command."""

from __future__ import annotations

import re
import socket
import ssl
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from meridian.cli import app
from meridian.commands.probe import (
    _NGINX_STOCK_LENGTH,
    _cert_identity,
    check_http2_support,
    check_http_response,
    check_legacy_tls,
    check_ports,
    check_proxy_paths,
    check_reverse_dns,
    check_secret_paths,
    check_sni_camouflage,
    check_sni_consistency,
    check_tls_certificate,
    check_websocket_upgrade,
)

runner = CliRunner()

# RFC 5737 test IP
_TEST_IP = "198.51.100.1"


def _finding_messages(result) -> list[str]:  # noqa: ANN001
    """Extract just the message strings from findings."""
    return [msg for _, msg in result.findings]


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


# ---------------------------------------------------------------------------
# Check 1: Port surface
# ---------------------------------------------------------------------------


class TestCheckPorts:
    def test_only_443_open_passes(self) -> None:
        with patch("meridian.commands.probe.tcp_connect") as mock_tcp:
            mock_tcp.side_effect = lambda ip, port, timeout=3: port == 443
            result = check_ports(_TEST_IP)
        assert result.passed
        assert any("443" in msg for msg in _finding_messages(result))

    def test_suspicious_port_open_warns(self) -> None:
        with (
            patch("meridian.commands.probe.tcp_connect") as mock_tcp,
            patch("meridian.commands.probe._https_get", return_value=(200, {}, b"panel")),
        ):
            mock_tcp.side_effect = lambda ip, port, timeout=3: port in (443, 2053)
            result = check_ports(_TEST_IP)
        assert not result.passed
        assert any("2053" in msg for msg in _finding_messages(result))

    def test_port_443_closed_fails_immediately(self) -> None:
        with patch("meridian.commands.probe.tcp_connect", return_value=False):
            result = check_ports(_TEST_IP)
        assert not result.passed
        assert any("not reachable" in msg for msg in _finding_messages(result))

    def test_port_80_open_is_acceptable(self) -> None:
        with patch("meridian.commands.probe.tcp_connect") as mock_tcp:
            mock_tcp.side_effect = lambda ip, port, timeout=3: port in (443, 80)
            result = check_ports(_TEST_IP)
        assert result.passed
        assert any("80" in msg for msg in _finding_messages(result))

    def test_findings_carry_correct_status(self) -> None:
        with (
            patch("meridian.commands.probe.tcp_connect") as mock_tcp,
            patch("meridian.commands.probe._https_get", return_value=(200, {}, b"panel")),
        ):
            mock_tcp.side_effect = lambda ip, port, timeout=3: port in (443, 2053)
            result = check_ports(_TEST_IP)
        # Port 443 finding should be ok=True, port 2053 should be ok=False
        for is_ok, msg in result.findings:
            if "443" in msg:
                assert is_ok
            if "2053" in msg:
                assert not is_ok

    def test_suspicious_port_middlebox_passes(self) -> None:
        """TCP handshake completes but no real service — middlebox, not a real issue."""
        with (
            patch("meridian.commands.probe.tcp_connect") as mock_tcp,
            patch("meridian.commands.probe._https_get", return_value=(0, {}, b"")),
        ):
            mock_tcp.side_effect = lambda ip, port, timeout=3: port in (443, 8080)
            result = check_ports(_TEST_IP)
        assert result.passed
        assert any("no service" in msg for msg in _finding_messages(result))

    def test_multiple_middlebox_ports_still_passes(self) -> None:
        """All suspicious ports TCP-reachable but none serve real content."""
        with (
            patch("meridian.commands.probe.tcp_connect", return_value=True),
            patch("meridian.commands.probe._https_get", return_value=(0, {}, b"")),
        ):
            result = check_ports(_TEST_IP)
        assert result.passed


# ---------------------------------------------------------------------------
# Check 2: HTTP response
# ---------------------------------------------------------------------------


class TestCheckHttpResponse:
    def test_stock_nginx_403_passes(self) -> None:
        stock_body = b"x" * _NGINX_STOCK_LENGTH
        with patch("meridian.commands.probe._https_get") as mock:
            mock.side_effect = [
                (403, {"server": "nginx"}, stock_body),
                (404, {"server": "nginx"}, stock_body),
            ]
            result = check_http_response(_TEST_IP)
        assert result.passed

    def test_custom_error_page_warns(self) -> None:
        custom_body = b"<html><body>Custom VPN Panel</body></html>"
        with patch("meridian.commands.probe._https_get") as mock:
            mock.side_effect = [
                (403, {"server": "nginx"}, custom_body),
                (404, {"server": "nginx"}, b"x" * _NGINX_STOCK_LENGTH),
            ]
            result = check_http_response(_TEST_IP)
        assert not result.passed
        assert any("Custom error page" in msg for msg in _finding_messages(result))

    def test_server_version_leak_warns(self) -> None:
        stock_body = b"x" * _NGINX_STOCK_LENGTH
        with patch("meridian.commands.probe._https_get") as mock:
            mock.side_effect = [
                (403, {"server": "nginx/1.24.0"}, stock_body),
                (404, {"server": "nginx/1.24.0"}, stock_body),
            ]
            result = check_http_response(_TEST_IP)
        assert not result.passed
        assert any("version" in msg.lower() for msg in _finding_messages(result))

    def test_non_403_status_warns(self) -> None:
        with patch("meridian.commands.probe._https_get") as mock:
            mock.side_effect = [
                (200, {"server": "nginx"}, b"<html>Welcome</html>"),
                (200, {"server": "nginx"}, b"<html>Not Found</html>"),
            ]
            result = check_http_response(_TEST_IP)
        assert not result.passed

    def test_connection_failure_skips(self) -> None:
        with patch("meridian.commands.probe._https_get", return_value=(0, {}, b"")):
            result = check_http_response(_TEST_IP)
        assert result.passed
        assert any("skipped" in msg.lower() for msg in _finding_messages(result))


# ---------------------------------------------------------------------------
# Check 3: TLS certificate
# ---------------------------------------------------------------------------


class TestCheckTlsCertificate:
    def test_no_domain_leak_passes(self) -> None:
        cert_text = """Certificate:
    Issuer: C=US, O=Let's Encrypt, CN=R3
    Subject:
    X509v3 Subject Alternative Name: critical
        IP Address:198.51.100.1"""
        with patch("meridian.commands.probe._get_cert_text_via_openssl", return_value=cert_text):
            result = check_tls_certificate(_TEST_IP)
        assert result.passed
        assert any("no domain" in msg.lower() for msg in _finding_messages(result))

    def test_domain_in_san_warns(self) -> None:
        cert_text = """Certificate:
    Issuer: C=US, O=Let's Encrypt, CN=R3
    Subject: CN=my-vpn.example.com
    X509v3 Subject Alternative Name:
        DNS:my-vpn.example.com, DNS:vpn.example.com"""
        with patch("meridian.commands.probe._get_cert_text_via_openssl", return_value=cert_text):
            result = check_tls_certificate(_TEST_IP)
        assert not result.passed
        assert any("my-vpn.example.com" in msg for msg in _finding_messages(result))

    def test_openssl_not_available_degrades_gracefully(self) -> None:
        with (
            patch("meridian.commands.probe._get_cert_text_via_openssl", return_value=""),
            patch("meridian.commands.probe._get_cert_der", return_value=b"\x30\x82"),
        ):
            result = check_tls_certificate(_TEST_IP)
        assert result.passed
        assert any("install openssl" in msg for msg in _finding_messages(result))

    def test_tls_handshake_fails_skips(self) -> None:
        with (
            patch("meridian.commands.probe._get_cert_text_via_openssl", return_value=""),
            patch("meridian.commands.probe._get_cert_der", return_value=b""),
        ):
            result = check_tls_certificate(_TEST_IP)
        assert result.passed
        assert any("skipped" in msg.lower() for msg in _finding_messages(result))


# ---------------------------------------------------------------------------
# Check 4: SNI consistency
# ---------------------------------------------------------------------------


class TestCheckSniConsistency:
    def test_identical_certs_passes(self) -> None:
        same_cert = b"\x30\x82\x01\x00" + b"\x00" * 256
        with (
            patch("meridian.commands.probe._get_cert_der", return_value=same_cert),
            patch("meridian.commands.probe._cert_identity", return_value="same-identity"),
        ):
            result = check_sni_consistency(_TEST_IP)
        assert result.passed
        assert any("consistent" in msg for msg in _finding_messages(result))

    def test_different_certs_warns(self) -> None:
        call_count = 0

        def varying_cert(ip: str, sni: str, timeout: int = 5) -> bytes:
            nonlocal call_count
            call_count += 1
            return b"\x30\x82" + call_count.to_bytes(2, "big") + b"\x00" * 256

        identity_count = 0

        def varying_identity(der: bytes) -> str:
            nonlocal identity_count
            identity_count += 1
            return f"identity-{identity_count}"

        with (
            patch("meridian.commands.probe._get_cert_der", side_effect=varying_cert),
            patch("meridian.commands.probe._cert_identity", side_effect=varying_identity),
        ):
            result = check_sni_consistency(_TEST_IP)
        assert not result.passed
        assert any("inconsistent" in msg for msg in _finding_messages(result))

    def test_same_identity_different_der_passes(self) -> None:
        """CDN scenario: different cert bytes but same subject+issuer."""
        call_count = 0

        def varying_cert(ip: str, sni: str, timeout: int = 5) -> bytes:
            nonlocal call_count
            call_count += 1
            return b"\x30\x82" + call_count.to_bytes(2, "big") + b"\x00" * 256

        with (
            patch("meridian.commands.probe._get_cert_der", side_effect=varying_cert),
            patch(
                "meridian.commands.probe._cert_identity",
                return_value="subject=CN=cdn.example.com|issuer=O=DigiCert",
            ),
        ):
            result = check_sni_consistency(_TEST_IP)
        assert result.passed
        assert any("consistent" in msg for msg in _finding_messages(result))


# ---------------------------------------------------------------------------
# Check 5: Proxy paths
# ---------------------------------------------------------------------------


class TestCheckProxyPaths:
    def test_all_paths_identical_passes(self) -> None:
        stock = b"x" * _NGINX_STOCK_LENGTH
        with patch("meridian.commands.probe._https_get", return_value=(404, {}, stock)):
            result = check_proxy_paths(_TEST_IP)
        assert result.passed

    def test_websocket_101_on_path_warns(self) -> None:
        stock = b"x" * _NGINX_STOCK_LENGTH

        def path_response(
            ip: str,
            path: str,
            timeout: int = 5,
            extra_headers: dict | None = None,
        ) -> tuple:
            if path == "/ws":
                return (101, {}, b"")
            return (404, {}, stock)

        with patch("meridian.commands.probe._https_get", side_effect=path_response):
            result = check_proxy_paths(_TEST_IP)
        assert not result.passed
        assert any("101" in msg for msg in _finding_messages(result))


# ---------------------------------------------------------------------------
# Check 6: WebSocket upgrade
# ---------------------------------------------------------------------------


class TestCheckWebsocketUpgrade:
    def test_upgrade_rejected_passes(self) -> None:
        with patch("meridian.commands.probe._https_get", return_value=(403, {}, b"")):
            result = check_websocket_upgrade(_TEST_IP)
        assert result.passed

    def test_upgrade_accepted_warns(self) -> None:
        with patch("meridian.commands.probe._https_get", return_value=(101, {}, b"")):
            result = check_websocket_upgrade(_TEST_IP)
        assert not result.passed
        assert any("accepts" in msg.lower() for msg in _finding_messages(result))


# ---------------------------------------------------------------------------
# Check 7: Reverse DNS
# ---------------------------------------------------------------------------


class TestCheckReverseDns:
    def test_no_ptr_record_passes(self) -> None:
        with patch("meridian.commands.probe.socket.gethostbyaddr", side_effect=socket.herror):
            result = check_reverse_dns(_TEST_IP)
        assert result.passed
        assert any("No reverse DNS" in msg for msg in _finding_messages(result))

    def test_hosting_ptr_passes(self) -> None:
        with patch(
            "meridian.commands.probe.socket.gethostbyaddr",
            return_value=("v12345.hosted-by-vdsina.com", [], []),
        ):
            result = check_reverse_dns(_TEST_IP)
        assert result.passed
        assert any("vdsina" in msg for msg in _finding_messages(result))

    def test_normal_ptr_passes(self) -> None:
        with patch(
            "meridian.commands.probe.socket.gethostbyaddr",
            return_value=("mail.example.com", [], []),
        ):
            result = check_reverse_dns(_TEST_IP)
        assert result.passed
        assert any("mail.example.com" in msg for msg in _finding_messages(result))


# ---------------------------------------------------------------------------
# Check 8: HTTP/2 support
# ---------------------------------------------------------------------------


class TestCheckHttp2Support:
    def test_h2_supported_passes(self) -> None:
        mock_ssock = MagicMock()
        mock_ssock.selected_alpn_protocol.return_value = "h2"

        with (
            patch("meridian.commands.probe.socket.create_connection"),
            patch("meridian.commands.probe.ssl.SSLContext") as mock_ctx_cls,
        ):
            mock_ctx_cls.return_value.wrap_socket.return_value = mock_ssock
            result = check_http2_support(_TEST_IP)
        assert result.passed
        assert any("HTTP/2" in msg for msg in _finding_messages(result))

    def test_h1_only_warns(self) -> None:
        mock_ssock = MagicMock()
        mock_ssock.selected_alpn_protocol.return_value = "http/1.1"

        with (
            patch("meridian.commands.probe.socket.create_connection"),
            patch("meridian.commands.probe.ssl.SSLContext") as mock_ctx_cls,
        ):
            mock_ctx_cls.return_value.wrap_socket.return_value = mock_ssock
            result = check_http2_support(_TEST_IP)
        assert not result.passed
        assert any("HTTP/1.1" in msg for msg in _finding_messages(result))


# ---------------------------------------------------------------------------
# Check 9: Legacy TLS
# ---------------------------------------------------------------------------


class TestCheckLegacyTls:
    def test_no_legacy_passes(self) -> None:
        with patch("meridian.commands.probe._tls_version_accepted", return_value=False):
            result = check_legacy_tls(_TEST_IP)
        assert result.passed
        assert any("TLS 1.2+" in msg for msg in _finding_messages(result))

    def test_tls10_accepted_warns(self) -> None:
        def accept_tls10(ip: str, version: ssl.TLSVersion) -> bool:
            return version == ssl.TLSVersion.TLSv1

        with patch("meridian.commands.probe._tls_version_accepted", side_effect=accept_tls10):
            result = check_legacy_tls(_TEST_IP)
        assert not result.passed
        assert any("TLS 1.0" in msg for msg in _finding_messages(result))

    def test_both_legacy_accepted_warns(self) -> None:
        with patch("meridian.commands.probe._tls_version_accepted", return_value=True):
            result = check_legacy_tls(_TEST_IP)
        assert not result.passed
        assert any("TLS 1.0 + TLS 1.1" in msg for msg in _finding_messages(result))


# ---------------------------------------------------------------------------
# _cert_identity helper
# ---------------------------------------------------------------------------


class TestCertIdentity:
    def test_openssl_extracts_subject_issuer(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = b"subject=CN=example.com\nissuer=O=DigiCert"
        with patch("meridian.commands.probe.subprocess.run", return_value=mock_result):
            identity = _cert_identity(b"\x30\x82\x00")
        assert "example.com" in identity
        assert "DigiCert" in identity

    def test_fallback_without_openssl(self) -> None:
        with patch("meridian.commands.probe.subprocess.run", side_effect=FileNotFoundError):
            identity = _cert_identity(b"\x30\x82\x00")
        assert len(identity) == 64  # sha256 hex

    def test_openssl_failure_falls_back(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = b""
        with patch("meridian.commands.probe.subprocess.run", return_value=mock_result):
            identity = _cert_identity(b"\x30\x82\x00")
        assert len(identity) == 64  # sha256 hex


# ---------------------------------------------------------------------------
# Check 10: Internal ports (deployment-aware)
# ---------------------------------------------------------------------------


class TestCheckInternalPorts:
    def test_all_closed_passes(self) -> None:
        from meridian.commands.probe import check_internal_ports

        with patch("meridian.commands.probe.tcp_connect", return_value=False):
            result = check_internal_ports(_TEST_IP, has_domain=False)
        assert result.passed
        assert any("closed externally" in msg for msg in _finding_messages(result))

    def test_exposed_xhttp_port_fails(self) -> None:
        from meridian.commands.probe import _compute_internal_ports, check_internal_ports

        ports = _compute_internal_ports(_TEST_IP, has_domain=False)
        xhttp_port = ports["xhttp"]

        def selective_connect(ip: str, port: int, timeout: int = 3) -> bool:
            return port == xhttp_port

        with patch("meridian.commands.probe.tcp_connect", side_effect=selective_connect):
            result = check_internal_ports(_TEST_IP, has_domain=False)
        assert not result.passed
        assert any("xhttp" in msg for msg in _finding_messages(result))

    def test_domain_mode_includes_reality_port(self) -> None:
        from meridian.commands.probe import _compute_internal_ports

        ports = _compute_internal_ports(_TEST_IP, has_domain=True)
        assert "reality" in ports
        assert ports["reality"] != 443

    def test_standalone_mode_omits_reality_port(self) -> None:
        from meridian.commands.probe import _compute_internal_ports

        ports = _compute_internal_ports(_TEST_IP, has_domain=False)
        assert "reality" not in ports

    def test_port_computation_is_deterministic(self) -> None:
        from meridian.commands.probe import _compute_internal_ports

        ports1 = _compute_internal_ports(_TEST_IP, has_domain=True)
        ports2 = _compute_internal_ports(_TEST_IP, has_domain=True)
        assert ports1 == ports2

    def test_port_computation_matches_setup_algorithm(self) -> None:
        """Port derivation must match the hashlib-based algorithm in setup.py."""
        import hashlib as hl

        from meridian.commands.probe import _compute_internal_ports

        ip_hash = int(hl.sha256(_TEST_IP.encode()).hexdigest()[:8], 16)
        ports = _compute_internal_ports(_TEST_IP, has_domain=True)
        assert ports["xhttp"] == 30000 + (ip_hash % 10000)
        assert ports["wss"] == 20000 + (ip_hash % 10000)
        assert ports["reality"] == 10000 + (ip_hash % 1000)


# ---------------------------------------------------------------------------
# Check 11: Root indistinguishable (deployment-aware)
# ---------------------------------------------------------------------------


class TestCheckRootIndistinguishable:
    def test_all_stock_responses_passes(self) -> None:
        from meridian.commands.probe import check_root_indistinguishable

        with patch("meridian.commands.probe._https_get", return_value=(404, {}, b"")):
            result = check_root_indistinguishable(_TEST_IP)
        assert result.passed

    def test_favicon_returns_200_fails(self) -> None:
        from meridian.commands.probe import check_root_indistinguishable

        def path_response(ip: str, path: str, **kwargs: object) -> tuple[int, dict[str, str], bytes]:
            if path == "/favicon.ico":
                return 200, {}, b"icon-data"
            return 404, {}, b""

        with patch("meridian.commands.probe._https_get", side_effect=path_response):
            result = check_root_indistinguishable(_TEST_IP)
        assert not result.passed
        assert any("favicon" in msg for msg in _finding_messages(result))

    def test_api_returns_200_fails(self) -> None:
        from meridian.commands.probe import check_root_indistinguishable

        def path_response(ip: str, path: str, **kwargs: object) -> tuple[int, dict[str, str], bytes]:
            if path == "/api":
                return 200, {}, b'{"status":"ok"}'
            return 404, {}, b""

        with patch("meridian.commands.probe._https_get", side_effect=path_response):
            result = check_root_indistinguishable(_TEST_IP)
        assert not result.passed
        assert any("API" in msg for msg in _finding_messages(result))

    def test_connection_failure_skips(self) -> None:
        from meridian.commands.probe import check_root_indistinguishable

        with patch("meridian.commands.probe._https_get", return_value=(0, {}, b"")):
            result = check_root_indistinguishable(_TEST_IP)
        assert result.passed
        assert any("skipped" in msg.lower() for msg in _finding_messages(result))


# ---------------------------------------------------------------------------
# Check 12: Domain root (deployment-aware)
# ---------------------------------------------------------------------------


class TestCheckDomainRoot:
    def test_403_root_passes(self) -> None:
        from meridian.commands.probe import check_domain_root

        with patch("meridian.commands.probe._https_get") as mock:
            mock.side_effect = [
                (403, {}, b""),  # Host: domain root
                (404, {}, b"Not Found"),  # ACME path
            ]
            result = check_domain_root(_TEST_IP, "vpn.example.com")
        assert result.passed
        assert any("vpn.example.com" in msg for msg in _finding_messages(result))

    def test_root_returns_200_fails(self) -> None:
        from meridian.commands.probe import check_domain_root

        with patch("meridian.commands.probe._https_get") as mock:
            mock.side_effect = [
                (200, {}, b"<html>Welcome</html>"),  # bad
                (404, {}, b""),  # ACME
            ]
            result = check_domain_root(_TEST_IP, "vpn.example.com")
        assert not result.passed
        assert any("expected 403/404" in msg for msg in _finding_messages(result))

    def test_acme_directory_listing_fails(self) -> None:
        from meridian.commands.probe import check_domain_root

        with patch("meridian.commands.probe._https_get") as mock:
            mock.side_effect = [
                (403, {}, b""),  # root OK
                (200, {}, b"<html><pre>Index of /.well-known/</pre></html>"),  # directory listing
            ]
            result = check_domain_root(_TEST_IP, "vpn.example.com")
        assert not result.passed
        assert any("directory listing" in msg for msg in _finding_messages(result))

    def test_connection_failure_skips(self) -> None:
        from meridian.commands.probe import check_domain_root

        with patch("meridian.commands.probe._https_get", return_value=(0, {}, b"")):
            result = check_domain_root(_TEST_IP, "vpn.example.com")
        assert result.passed
        assert any("skipped" in msg.lower() for msg in _finding_messages(result))


# ---------------------------------------------------------------------------
# Check: Secret path isolation
# ---------------------------------------------------------------------------


class TestCheckSecretPaths:
    def test_common_panel_paths_return_403_passes(self) -> None:
        def path_response(ip: str, path: str, **kwargs: object) -> tuple[int, dict, bytes]:
            if path.startswith("/secret-panel"):
                return (200, {}, b"panel")
            return (403, {}, b"")

        with patch("meridian.commands.probe._https_get", side_effect=path_response):
            result = check_secret_paths(_TEST_IP, "secret-panel")
        assert result.passed
        assert any("accessible" in msg for msg in _finding_messages(result))

    def test_panel_path_returning_200_fails(self) -> None:
        def path_response(ip: str, path: str, **kwargs: object) -> tuple[int, dict, bytes]:
            if path == "/admin":
                return (200, {}, b"<html>Admin</html>")
            if path.startswith("/secret"):
                return (200, {}, b"panel")
            return (403, {}, b"")

        with patch("meridian.commands.probe._https_get", side_effect=path_response):
            result = check_secret_paths(_TEST_IP, "secret")
        assert not result.passed
        assert any("/admin" in msg for msg in _finding_messages(result))

    def test_secret_path_accessible_passes(self) -> None:
        def path_response(ip: str, path: str, **kwargs: object) -> tuple[int, dict, bytes]:
            if path == "/my-secret/":
                return (301, {}, b"")
            return (404, {}, b"")

        with patch("meridian.commands.probe._https_get", side_effect=path_response):
            result = check_secret_paths(_TEST_IP, "my-secret")
        assert result.passed
        assert any("accessible" in msg.lower() or "Panel" in msg for msg in _finding_messages(result))


# ---------------------------------------------------------------------------
# Check: SNI camouflage
# ---------------------------------------------------------------------------


class TestCheckSniCamouflage:
    def test_matching_certs_passes(self) -> None:
        same_cert = b"\x30\x82\x01\x00" + b"\x00" * 256
        with (
            patch("meridian.commands.probe._get_cert_der", return_value=same_cert),
            patch("meridian.commands.probe._cert_identity", return_value="same-identity"),
        ):
            result = check_sni_camouflage(_TEST_IP, "www.microsoft.com")
        assert result.passed
        assert any("matching" in msg for msg in _finding_messages(result))

    def test_mismatched_certs_fails(self) -> None:
        call_count = 0

        def varying_cert(ip: str, sni: str, timeout: int = 5) -> bytes:
            nonlocal call_count
            call_count += 1
            return b"\x30\x82" + call_count.to_bytes(2, "big") + b"\x00" * 256

        identity_count = 0

        def varying_identity(der: bytes) -> str:
            nonlocal identity_count
            identity_count += 1
            return f"identity-{identity_count}"

        with (
            patch("meridian.commands.probe._get_cert_der", side_effect=varying_cert),
            patch("meridian.commands.probe._cert_identity", side_effect=varying_identity),
        ):
            result = check_sni_camouflage(_TEST_IP, "www.microsoft.com")
        assert not result.passed
        assert any("NOT match" in msg for msg in _finding_messages(result))

    def test_connection_failure_skips(self) -> None:
        with patch("meridian.commands.probe._get_cert_der", return_value=b""):
            result = check_sni_camouflage(_TEST_IP, "www.microsoft.com")
        assert result.passed
        assert any("skipped" in msg.lower() or "Could not" in msg for msg in _finding_messages(result))


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


class TestProbeCLI:
    def test_probe_help(self) -> None:
        result = runner.invoke(app, ["probe", "--help"])
        assert result.exit_code == 0
        output = _strip_ansi(result.output)
        assert "censor" in output.lower()

    def test_probe_shows_in_main_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "probe" in _strip_ansi(result.output)
