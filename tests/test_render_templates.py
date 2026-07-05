"""Pytest version of template rendering tests.

Auto-discovers all .j2 templates under src/meridian/templates/ and
renders each with a mock variable context. Failures are reported as proper
pytest assertions (one test case per template file).

The standalone render_templates.py script is kept for CI use — this file
is the pytest counterpart.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, Undefined

from meridian.models import ProtocolURL, RelayURLSet
from meridian.render import _generate_minimal_html, _render_template, render_hosted_html

# ---------------------------------------------------------------------------
# Mock infrastructure (mirrors render_templates.py)
# ---------------------------------------------------------------------------


class MockUndefined(Undefined):
    """Permissive undefined that never crashes on missing vars or filters."""

    def __str__(self) -> str:
        return ""

    def __bool__(self) -> bool:
        return False

    def __iter__(self):  # type: ignore[override]
        return iter([])

    def __getattr__(self, name: str) -> "MockUndefined":
        return MockUndefined()

    def __call__(self, *args: object, **kwargs: object) -> "MockUndefined":
        return MockUndefined()


def _mock_bool(value: object) -> bool:
    if isinstance(value, str):
        return value.lower() in ("true", "yes", "1")
    return bool(value)


def _mock_default(value: object, default_value: object = "", boolean: bool = False) -> object:
    if value is None or isinstance(value, Undefined):
        return default_value
    if boolean and not value:
        return default_value
    return value


def _mock_regex_search(value: object, pattern: str, *args: object) -> object:
    match = re.search(pattern, str(value))
    if match:
        if match.groups():
            return list(match.groups())
        return match.group(0)
    return None


def _mock_hash(value: object, method: str = "sha1") -> str:
    return "a1b2c3d4e5f6"


def _mock_int(value: object, default: int = 0, base: int = 10) -> int:
    try:
        return int(str(value), base)
    except (ValueError, TypeError):
        return default


class MockResult:
    """Mock for registered task results (e.g., qrencode output)."""

    def __init__(self, stdout: str = "dGVzdA==") -> None:
        self.stdout = stdout


MOCK_VARS: dict[str, object] = {
    "domain": "example.com",
    "email": "",
    "domain_mode": True,
    "panel_internal_port": 2053,
    "panel_external_port": 12345,
    "panel_web_base_path": "testpath123",
    "panel_username": "testuser",
    "panel_password": "testpass",
    "info_page_path": "testinfo456",
    "ws_path": "testws789",
    "nginx_internal_port": 8443,
    "wss_internal_port": 28000,
    "reality_backend_port": 10443,
    "reality_sni": "www.cloudflare.com",
    "reality_dest": "www.cloudflare.com:443",
    "server_public_ip": "1.2.3.4",
    "inventory_hostname": "proxy",
    "generated_at": {"iso8601": "2026-01-01T00:00:00Z", "year": "2026"},
    "threexui_version": "2.8.11",
    "utls_fingerprint": "chrome",
    "xhttp_mode": "packet-up",
    "xhttp_path": "/",
    "credentials_dir": "/tmp/credentials",
    "credentials_file": "/tmp/credentials/proxy.yml",
    "vless_reality_url": "vless://test-uuid@1.2.3.4:443?security=reality#Test",
    "vless_wss_url": "vless://test-uuid@example.com:443?security=tls#Test",
    "reality_qr_b64": "dGVzdA==",
    "wss_qr_b64": "dGVzdA==",
    "xhttp_qr_b64": "dGVzdA==",
    "reality_qr_terminal": MockResult(stdout="QR_CODE_HERE"),
    "wss_qr_terminal": MockResult(stdout="QR_CODE_HERE"),
    "port_443_check": MockResult(stdout="LISTEN 0 4096 *:443"),
    "reality_uuid": "test-uuid",
    "reality_public_key": "test-pubkey",
    "reality_short_id": "abcd1234",
    "xhttp_enabled": True,
    "xhttp_inbound_port": 34567,
    "vless_xhttp_url": "vless://test-uuid@1.2.3.4:443?security=tls&type=xhttp&path=%2Ftestxhttp#Test-XHTTP",
    "xhttp_qr_terminal": MockResult(stdout="QR_CODE_HERE"),
    "port_xhttp_check": MockResult(stdout="LISTEN 0 4096 *:34567"),
    "client_name": "default",
    "first_client_name": "default",
    "is_server_hosted": True,
    "asset_path": "../pwa",
}


def _make_env(template_dir: Path) -> Environment:
    """Create a Jinja2 Environment with template filters and mocks."""
    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        undefined=MockUndefined,
    )
    env.filters["bool"] = _mock_bool
    env.filters["default"] = _mock_default
    env.filters["d"] = _mock_default
    env.filters["regex_search"] = _mock_regex_search
    env.filters["hash"] = _mock_hash
    env.filters["int"] = _mock_int
    env.filters["trim"] = lambda x: str(x).strip()
    env.filters["replace"] = lambda x, old, new: str(x).replace(old, new)
    env.filters["length"] = len
    env.filters["lower"] = lambda x: str(x).lower()
    env.filters["upper"] = lambda x: str(x).upper()
    env.filters["to_json"] = lambda x: str(x)
    env.tests["defined"] = lambda x: not isinstance(x, Undefined)
    env.tests["undefined"] = lambda x: isinstance(x, Undefined)
    env.tests["none"] = lambda x: x is None
    env.tests["succeeded"] = lambda x: True
    env.tests["failed"] = lambda x: False
    return env


# ---------------------------------------------------------------------------
# Template discovery
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).parent.parent / "src" / "meridian" / "templates"
_TEMPLATES = list(_TEMPLATES_DIR.glob("**/*.j2"))


def _template_id(p: Path) -> str:
    """Human-readable pytest ID: pwa/index.html.j2 or connection-info.html.j2"""
    rel = p.relative_to(_TEMPLATES_DIR)
    return str(rel)


# ---------------------------------------------------------------------------
# Parametrized tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "template_path",
    sorted(_TEMPLATES),
    ids=[_template_id(p) for p in sorted(_TEMPLATES)],
)
def test_template_renders(template_path: Path) -> None:
    """Each .j2 template must render without exceptions using mock variables."""
    env = _make_env(template_path.parent)
    template = env.get_template(template_path.name)
    result = template.render(**MOCK_VARS)
    # Sanity check: non-trivial output
    assert len(result) >= 1, f"Template rendered empty output: {template_path}"


def test_templates_discovered() -> None:
    """Sanity check that template auto-discovery finds at least a few templates."""
    assert len(_TEMPLATES) >= 1, f"Expected at least 1 template under {_TEMPLATES_DIR}, found {len(_TEMPLATES)}"


def test_connection_info_template_has_no_external_ping_dependency() -> None:
    env = _make_env(_TEMPLATES_DIR)
    template = env.get_template("connection-info.html.j2")
    result = template.render(**MOCK_VARS)
    assert "getmeridian.org/ping" not in result
    assert "meridian test" in result


# ---------------------------------------------------------------------------
# Content-level tests — verify rendered HTML contains the right data
# ---------------------------------------------------------------------------


# Shared test fixtures (RFC 5737 IPs)
_TEST_REALITY_URL = (
    "vless://550e8400-e29b-41d4-a716-446655440000@198.51.100.1:443?security=reality&sni=www.example.com#TestClient"
)
_TEST_WSS_URL = "vless://660e8400-e29b-41d4-a716-446655440000@example.com:443?security=tls&type=ws#TestClient-WSS"
_TEST_QR_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"


def _make_protocol_urls(*, include_wss: bool = False, qr_b64: str = _TEST_QR_B64) -> list[ProtocolURL]:
    """Build a minimal list of ProtocolURL objects for testing."""
    urls = [
        ProtocolURL(key="reality", label="Primary", url=_TEST_REALITY_URL, qr_b64=qr_b64),
    ]
    if include_wss:
        urls.append(
            ProtocolURL(key="wss", label="CDN Backup", url=_TEST_WSS_URL, qr_b64=qr_b64),
        )
    return urls


def _make_relay_entries() -> list[RelayURLSet]:
    """Build relay entries for testing."""
    relay_url = (
        "vless://550e8400-e29b-41d4-a716-446655440000@198.51.100.50:443"
        "?security=reality&sni=www.example.com#TestClient-Relay"
    )
    return [
        RelayURLSet(
            relay_ip="198.51.100.50",
            relay_name="ru-moscow",
            urls=[
                ProtocolURL(
                    key="reality",
                    label="Primary (via relay)",
                    url=relay_url,
                    qr_b64=_TEST_QR_B64,
                ),
            ],
        ),
    ]


class TestRenderedHtmlContent:
    """Verify rendered HTML actually contains expected data, not just that it doesn't crash."""

    def test_rendered_html_contains_qr_data(self) -> None:
        """QR base64 string appears in the rendered HTML output."""
        urls = _make_protocol_urls(qr_b64=_TEST_QR_B64)
        html = render_hosted_html(urls, "198.51.100.1", client_name="alice")
        assert _TEST_QR_B64 in html

    def test_rendered_html_contains_vless_url(self) -> None:
        """VLESS Reality URL appears in the rendered HTML output."""
        urls = _make_protocol_urls()
        html = render_hosted_html(urls, "198.51.100.1", client_name="alice")
        assert _TEST_REALITY_URL in html or "550e8400-e29b-41d4-a716-446655440000" in html

    def test_rendered_html_escapes_client_name(self) -> None:
        """XSS payload in client_name is HTML-escaped, not rendered raw."""
        urls = _make_protocol_urls()
        html = render_hosted_html(urls, "198.51.100.1", client_name="<script>alert(1)</script>")
        # Raw <script> must NOT appear — it should be escaped
        assert "<script>alert(1)</script>" not in html
        # The escaped form should be present
        assert "&lt;script&gt;" in html or "&#" in html

    def test_rendered_html_contains_relay_data(self) -> None:
        """Relay IP and name appear in the rendered HTML when relay entries are provided."""
        urls = _make_protocol_urls()
        relay_entries = _make_relay_entries()
        html = render_hosted_html(urls, "198.51.100.1", client_name="alice", relay_entries=relay_entries)
        assert "198.51.100.50" in html
        assert "ru-moscow" in html

    def test_minimal_html_fallback(self) -> None:
        """_generate_minimal_html produces valid HTML with URL data when template is unavailable."""
        urls = _make_protocol_urls()
        qr_map = {p.key: p.qr_b64 for p in urls if p.qr_b64}
        html = _generate_minimal_html("alice", urls, qr_map, "198.51.100.1", "", "2026-01-01T00:00:00Z")
        assert "<!DOCTYPE html>" in html
        assert "</html>" in html
        assert "550e8400-e29b-41d4-a716-446655440000" in html
        assert _TEST_QR_B64 in html
        assert "alice" in html

    def test_minimal_html_via_render_template_none(self) -> None:
        """_render_template falls back to minimal HTML when template_text is None."""
        urls = _make_protocol_urls()
        variables = {
            "vless_reality_url": _TEST_REALITY_URL,
            "vless_xhttp_url": "",
            "vless_wss_url": "",
            "server_public_ip": "198.51.100.1",
            "domain": "",
            "domain_mode": False,
            "is_server_hosted": True,
            "client_name": "alice",
            "generated_at": {"iso8601": "2026-01-01T00:00:00Z"},
            "relays": [],
            "has_relays": False,
        }
        html = _render_template(
            template_text=None,
            variables=variables,
            protocol_urls=urls,
            server_ip="198.51.100.1",
            domain="",
            client_name="alice",
            now="2026-01-01T00:00:00Z",
        )
        assert "<!DOCTYPE html>" in html
        assert "550e8400-e29b-41d4-a716-446655440000" in html

    def test_domain_mode_shows_wss(self) -> None:
        """When domain is set, WSS/Backup section appears in the rendered HTML."""
        urls = _make_protocol_urls(include_wss=True)
        html = render_hosted_html(urls, "198.51.100.1", domain="example.com", client_name="alice")
        # WSS card uses "Backup" label and the amber color class in the template
        assert "Backup" in html or "card-amber" in html
        # The WSS URL should be present
        assert "660e8400-e29b-41d4-a716-446655440000" in html

    def test_ip_mode_hides_wss(self) -> None:
        """Without a domain, the WSS/Backup section is absent from the rendered HTML."""
        urls = _make_protocol_urls()
        html = render_hosted_html(urls, "198.51.100.1", client_name="alice")
        # The WSS URL should not appear
        assert _TEST_WSS_URL not in html
        # The "Backup" label div (actual WSS card content) should not be rendered
        # Note: "card-amber" appears in CSS definitions regardless, so we check
        # for the actual WSS card markup: <div class="card card-amber">
        assert '<div class="card card-amber">' not in html
