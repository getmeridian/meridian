"""Tests for PWA rendering functions (config.json, shell, manifest)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from meridian.models import ProtocolURL
from meridian.render import (
    _PWA_APPS,
    render_apps_catalog,
    render_config_json,
    render_manifest,
    render_pwa_shell,
)

REALITY_URL = (
    "vless://550e8400-e29b-41d4-a716-446655440000@198.51.100.1:443?security=reality&sni=www.example.com#Test-Reality"
)
XHTTP_URL = "vless://550e8400-e29b-41d4-a716-446655440000@198.51.100.1:443?security=tls&type=xhttp#Test-XHTTP"
WSS_URL = "vless://660e8400-e29b-41d4-a716-446655440000@example.com:443?security=tls&type=ws#Test-WSS"


@pytest.fixture()
def protocol_urls() -> list[ProtocolURL]:
    return [
        ProtocolURL(key="reality", label="Primary", url=REALITY_URL, qr_b64="dGVzdA=="),
        ProtocolURL(key="xhttp", label="XHTTP", url=XHTTP_URL, qr_b64="eGh0dHA="),
    ]


class TestRenderConfigJson:
    def test_valid_json(self, protocol_urls: list[ProtocolURL]) -> None:
        result = render_config_json(protocol_urls, "198.51.100.1")
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_basic_structure(self, protocol_urls: list[ProtocolURL]) -> None:
        result = json.loads(render_config_json(protocol_urls, "198.51.100.1"))
        assert result["version"] == 1
        assert result["server_ip"] == "198.51.100.1"
        assert "protocols" in result
        assert "apps" in result
        assert "generated_at" in result

    def test_includes_all_protocol_urls(self, protocol_urls: list[ProtocolURL]) -> None:
        result = json.loads(render_config_json(protocol_urls, "198.51.100.1"))
        keys = [p["key"] for p in result["protocols"]]
        assert "reality" in keys
        assert "xhttp" in keys

    def test_first_protocol_is_recommended(self, protocol_urls: list[ProtocolURL]) -> None:
        result = json.loads(render_config_json(protocol_urls, "198.51.100.1"))
        assert result["protocols"][0]["recommended"] is True
        assert result["protocols"][1]["recommended"] is False

    def test_client_name(self, protocol_urls: list[ProtocolURL]) -> None:
        result = json.loads(render_config_json(protocol_urls, "198.51.100.1", client_name="alice"))
        assert result["client_name"] == "alice"

    def test_domain(self, protocol_urls: list[ProtocolURL]) -> None:
        result = json.loads(render_config_json(protocol_urls, "198.51.100.1", domain="example.com"))
        assert result["domain"] == "example.com"

    def test_qr_data_preserved(self, protocol_urls: list[ProtocolURL]) -> None:
        result = json.loads(render_config_json(protocol_urls, "198.51.100.1"))
        assert result["protocols"][0]["qr_b64"] == "dGVzdA=="

    def test_empty_protocols(self) -> None:
        result = json.loads(render_config_json([], "198.51.100.1"))
        assert result["protocols"] == []

    def test_apps_list_present(self, protocol_urls: list[ProtocolURL]) -> None:
        result = json.loads(render_config_json(protocol_urls, "198.51.100.1"))
        assert len(result["apps"]) >= 3
        platforms = [a["platform"] for a in result["apps"]]
        assert "iOS" in platforms
        assert "Android" in platforms

    def test_subscription_url_and_qr_included_when_provided(self, protocol_urls: list[ProtocolURL]) -> None:
        result = json.loads(
            render_config_json(
                protocol_urls,
                "198.51.100.1",
                subscription_url="https://example.com/custom/subscription.txt",
            )
        )
        assert result["subscription_url"] == "https://example.com/custom/subscription.txt"
        assert result["subscription_qr_b64"]


class TestRenderPWAShell:
    def test_has_manifest_link(self) -> None:
        html = render_pwa_shell(client_name="test")
        assert 'rel="manifest"' in html
        assert "manifest.webmanifest" in html

    def test_has_app_js_script(self) -> None:
        html = render_pwa_shell()
        assert "app.js" in html

    def test_has_theme_color(self) -> None:
        html = render_pwa_shell()
        assert "theme-color" in html

    def test_has_noscript_fallback(self) -> None:
        html = render_pwa_shell()
        assert "<noscript>" in html

    def test_no_credentials_in_html(self) -> None:
        html = render_pwa_shell(client_name="alice")
        assert "vless://" not in html
        assert "qr_b64" not in html

    def test_has_referrer_policy(self) -> None:
        html = render_pwa_shell()
        assert "no-referrer" in html

    def test_client_name_in_title(self) -> None:
        html = render_pwa_shell(client_name="alice")
        assert "Alice" in html

    def test_custom_asset_path(self) -> None:
        html = render_pwa_shell(asset_path="/custom/path")
        assert "/custom/path/app.js" in html
        assert "/custom/path/styles.css" in html


class TestRenderManifest:
    def test_valid_json(self) -> None:
        result = render_manifest(client_name="test")
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_has_icons(self) -> None:
        result = json.loads(render_manifest())
        assert "icons" in result
        assert len(result["icons"]) >= 1

    def test_client_name_in_name(self) -> None:
        result = json.loads(render_manifest(client_name="alice"))
        assert "Alice" in result["name"]

    def test_display_standalone(self) -> None:
        result = json.loads(render_manifest())
        assert result["display"] == "standalone"

    def test_start_url_is_dot(self) -> None:
        result = json.loads(render_manifest())
        assert result["start_url"] == "."

    def test_icon_is_svg(self) -> None:
        result = json.loads(render_manifest())
        icon = result["icons"][0]
        assert icon["type"] == "image/svg+xml"
        assert icon["sizes"] == "any"

    def test_special_chars_produce_valid_json(self) -> None:
        result = render_manifest(client_name='test"quote')
        parsed = json.loads(result)
        assert "Test" in parsed["name"]

    def test_server_name_special_chars(self) -> None:
        result = render_manifest(server_name='My "Server"')
        parsed = json.loads(result)
        assert 'My "Server"' == parsed["name"]


class TestConfigJsonSchema:
    def test_protocol_entry_required_fields(self, protocol_urls: list[ProtocolURL]) -> None:
        result = json.loads(render_config_json(protocol_urls, "198.51.100.1"))
        required = {"key", "label", "url", "qr_b64", "recommended"}
        for entry in result["protocols"]:
            assert required.issubset(entry.keys()), f"Missing fields: {required - entry.keys()}"

    def test_version_is_integer_one(self, protocol_urls: list[ProtocolURL]) -> None:
        result = json.loads(render_config_json(protocol_urls, "198.51.100.1"))
        assert result["version"] == 1
        assert isinstance(result["version"], int)

    def test_apps_entry_required_fields(self, protocol_urls: list[ProtocolURL]) -> None:
        result = json.loads(render_config_json(protocol_urls, "198.51.100.1"))
        required = {"name", "platform", "url"}
        for app in result["apps"]:
            assert required.issubset(app.keys()), f"App missing fields: {required - app.keys()}"


class TestPWAApps:
    def test_deeplink_templates_contain_url_placeholder(self) -> None:
        for app in _PWA_APPS:
            if "deeplink" in app:
                has_url = "{url}" in app["deeplink"] or "{url_raw}" in app["deeplink"] or "{url_b64}" in app["deeplink"]
                assert has_url, f"{app['name']} deeplink missing URL placeholder: {app['deeplink']}"

    def test_protocols_field_present(self) -> None:
        for app in _PWA_APPS:
            assert "protocols" in app, f"{app['name']} missing protocols field"
            assert isinstance(app["protocols"], list), f"{app['name']} protocols must be a list"
            assert len(app["protocols"]) >= 1, f"{app['name']} protocols must not be empty"

    def test_protocols_values_valid(self) -> None:
        valid = {"reality", "xhttp", "wss"}
        for app in _PWA_APPS:
            for proto in app.get("protocols", []):
                assert proto in valid, f"{app['name']} has unknown protocol {proto!r}"


PLATFORMS = ("iOS", "Android", "Windows", "macOS", "Linux")


def _recommended(platform: str) -> list[str]:
    return [app["name"] for app in _PWA_APPS if platform in app.get("recommended", [])]


class TestAppCatalog:
    def test_every_platform_has_recommended_apps(self) -> None:
        for platform in PLATFORMS:
            assert _recommended(platform), f"no recommended app for {platform}"

    def test_happ_leads_every_platform(self) -> None:
        for platform in PLATFORMS:
            assert _recommended(platform)[0] == "Happ"

    def test_mihomo_alternatives_and_throne_on_desktop(self) -> None:
        assert _recommended("iOS") == ["Happ", "Clash Mi"]
        assert _recommended("Android") == ["Happ", "FlClashX", "Clash Mi"]
        for platform in ("Windows", "macOS", "Linux"):
            assert _recommended(platform) == ["Happ", "FlClashX", "Clash Mi", "Throne"]

    def test_recommended_platforms_are_supported(self) -> None:
        for app in _PWA_APPS:
            assert set(app["platforms"]) <= set(PLATFORMS), app["name"]
            assert set(app.get("recommended", [])) <= set(app["platforms"]), app["name"]

    def test_abandoned_nekoray_is_not_listed(self) -> None:
        assert not any("nekoray" in app["name"].lower() for app in _PWA_APPS)

    def test_deeplinks_use_only_known_placeholders(self) -> None:
        for app in _PWA_APPS:
            if "deeplink" in app:
                assert set(re.findall(r"\{(\w+)\}", app["deeplink"])) <= {"url_raw", "url_b64", "url", "name"}

    def test_download_links_are_https(self) -> None:
        for app in _PWA_APPS:
            for url in [app["url"], *app.get("urls", {}).values()]:
                assert url.startswith("https://"), f"{app['name']}: {url}"

    def test_config_json_apps_carry_no_icons(self, protocol_urls: list[ProtocolURL]) -> None:
        """Icons ship once in the shared pwa/apps.json, not in every client's config."""
        result = json.loads(render_config_json(protocol_urls, "198.51.100.1"))
        assert [app["name"] for app in result["apps"]] == [app["name"] for app in _PWA_APPS]
        assert not any("icon" in app for app in result["apps"])

    def test_shared_catalog_has_an_icon_for_every_app(self) -> None:
        catalog = json.loads(render_apps_catalog())
        assert [app["name"] for app in catalog["apps"]] == [app["name"] for app in _PWA_APPS]
        assert all("icon" in app for app in catalog["apps"])


class TestUnicodeClientNames:
    def test_config_json_cyrillic(self, protocol_urls: list[ProtocolURL]) -> None:
        result = render_config_json(protocol_urls, "198.51.100.1", client_name="\u0410\u043b\u0438\u0441\u0430")
        parsed = json.loads(result)
        assert parsed["client_name"] == "\u0410\u043b\u0438\u0441\u0430"

    def test_pwa_shell_farsi(self) -> None:
        html = render_pwa_shell(client_name="\u0633\u0627\u0631\u0627")
        # Should produce valid HTML without encoding errors
        assert "<!DOCTYPE html>" in html or "<html" in html

    def test_manifest_chinese(self) -> None:
        result = render_manifest(client_name="\u5c0f\u660e")
        json.loads(result)  # validate valid JSON
        # Chinese name should appear in the manifest name
        assert "\u5c0f\u660e" in result

    def test_config_json_emoji_name(self, protocol_urls: list[ProtocolURL]) -> None:
        name = "\u0645\u0631\u06cc\u0645"  # Farsi: Maryam
        result = render_config_json(protocol_urls, "198.51.100.1", client_name=name)
        parsed = json.loads(result)
        assert parsed["client_name"] == name


class TestPWATemplateSource:
    def test_app_js_prefers_canonical_subscription_url(self) -> None:
        app_js = Path("src/meridian/templates/pwa/app.js").read_text()
        assert "config.subscription_url" in app_js

    def test_app_js_has_no_external_ping_dependency(self) -> None:
        app_js = Path("src/meridian/templates/pwa/app.js").read_text()
        assert "getmeridian.org/ping" not in app_js

    def test_app_js_links_the_localized_router_guide(self) -> None:
        app_js = Path("src/meridian/templates/pwa/app.js").read_text()
        assert "'https://getmeridian.org/{lang}/routers/'" in app_js

    def test_app_js_prefers_the_shared_catalog(self) -> None:
        app_js = Path("src/meridian/templates/pwa/app.js").read_text()
        assert "fetch('../pwa/apps.json')" in app_js

    def test_every_data_t_key_is_translated(self) -> None:
        app_js = Path("src/meridian/templates/pwa/app.js").read_text()
        used = set(re.findall(r'data-t="([^"]+)"', app_js))
        for lang in ("ru", "fa", "zh"):
            block = app_js.split(f"\n  {lang}: {{\n", 1)[1].split("\n  },\n", 1)[0]
            keys = set(re.findall(r"'?([\w.]+)'?: '", block))
            assert used <= keys, f"{lang} lacks {sorted(used - keys)}"

    def test_rendered_markup_has_no_inline_styles(self) -> None:
        """The page CSP has no style-src 'unsafe-inline'; style attributes are dropped."""
        for name in ("app.js", "selfcheck.js"):
            assert "style=" not in (PWA_DIR / name).read_text(), name


PWA_DIR = Path("src/meridian/templates/pwa")


def _js_bytes(source: str, name: str) -> int:
    match = re.search(rf"var {name} = (\d+)(?: \* (\d+))?;", source)
    assert match, f"{name} not found"
    return int(match.group(1)) * int(match.group(2) or 1)


class TestSelfCheck:
    def test_thresholds_mirror_cli_blocking_diagnostics(self) -> None:
        from meridian.diagnostics import blocking

        source = (PWA_DIR / "selfcheck.js").read_text()
        assert _js_bytes(source, "STALL_CLEARANCE_BYTES") == blocking.STALL_CLEARANCE_BYTES
        assert _js_bytes(source, "THROTTLE_LOW_BYTES") == blocking.THROTTLE_WINDOW[0]
        assert _js_bytes(source, "STALL_TIMEOUT_MS") == 5000

    def test_clock_threshold_matches_the_cli_in_both_scripts(self) -> None:
        from meridian.core.defaults import CLOCK_DRIFT_WARN_SECONDS

        for name in ("selfcheck.js", "app.js"):
            source = (PWA_DIR / name).read_text()
            assert _js_bytes(source, "CLOCK_WARN_MS") == CLOCK_DRIFT_WARN_SECONDS * 1000, name

    def test_app_renders_the_section_and_loads_the_script(self) -> None:
        app_js = (PWA_DIR / "app.js").read_text()
        assert 'data-action="selfcheck-run"' in app_js
        assert 'id="selfcheck-results"' in app_js
        assert "s.src = '../pwa/selfcheck.js';" in app_js

    def test_probe_and_subscription_bypass_the_service_worker(self) -> None:
        sw = (PWA_DIR / "sw.js").read_text()
        assert "path.indexOf('/sub/') !== -1" in sw
        assert "path.endsWith('/probe.bin')" in sw
        assert "event.request.method !== 'GET'" in sw
        assert "'../pwa/selfcheck.js'" in sw

    def test_requests_skip_the_http_cache(self) -> None:
        source = (PWA_DIR / "selfcheck.js").read_text()
        assert source.count("cache: 'no-store'") == 3
        assert "PROBE_PATH + '?t=' + Date.now()" in source

    def test_report_never_carries_links_or_identifiers(self) -> None:
        source = (PWA_DIR / "selfcheck.js").read_text()
        report = source.split("function buildReport(results) {", 1)[1].split("\n}\n", 1)[0]
        for forbidden in ("url", "href", "location", "subscription", "_meridianConfig", "client_name"):
            assert forbidden not in report.lower(), forbidden

    def test_every_language_has_every_string(self) -> None:
        source = (PWA_DIR / "selfcheck.js").read_text()
        blocks = {
            lang: source.split(f"\n  {lang}: {{\n", 1)[1].split("\n  },\n", 1)[0] for lang in ("en", "ru", "fa", "zh")
        }
        keys = {lang: set(re.findall(r"'?([\w.]+)'?: '", block)) for lang, block in blocks.items()}
        for lang in ("ru", "fa", "zh"):
            assert keys[lang] == keys["en"], f"{lang}: {sorted(keys['en'] ^ keys[lang])}"
