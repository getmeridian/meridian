"""Tests for PWA rendering functions (config.json, subscription, shell, manifest)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from meridian.models import ProtocolURL, RelayURLSet
from meridian.render import (
    _PWA_APPS,
    render_config_json,
    render_manifest,
    render_pwa_shell,
    render_subscription,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

REALITY_URL = (
    "vless://550e8400-e29b-41d4-a716-446655440000@198.51.100.1:443?security=reality&sni=www.example.com#Test-Reality"
)
XHTTP_URL = "vless://550e8400-e29b-41d4-a716-446655440000@198.51.100.1:443?security=tls&type=xhttp#Test-XHTTP"
WSS_URL = "vless://660e8400-e29b-41d4-a716-446655440000@example.com:443?security=tls&type=ws#Test-WSS"
RELAY_URL = (
    "vless://550e8400-e29b-41d4-a716-446655440000@203.0.113.10:443?security=reality&sni=www.example.com#Test-Relay"
)


@pytest.fixture()
def protocol_urls() -> list[ProtocolURL]:
    return [
        ProtocolURL(key="reality", label="Primary", url=REALITY_URL, qr_b64="dGVzdA=="),
        ProtocolURL(key="xhttp", label="XHTTP", url=XHTTP_URL, qr_b64="eGh0dHA="),
    ]


@pytest.fixture()
def relay_entries() -> list[RelayURLSet]:
    return [
        RelayURLSet(
            relay_ip="203.0.113.10",
            relay_name="ru-moscow",
            urls=[ProtocolURL(key="reality", label="Primary (via relay)", url=RELAY_URL, qr_b64="cmVsYXk=")],
        ),
    ]


# ---------------------------------------------------------------------------
# TestRenderConfigJson
# ---------------------------------------------------------------------------


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
        assert "relays" in result
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

    def test_relay_entries_included(
        self,
        protocol_urls: list[ProtocolURL],
        relay_entries: list[RelayURLSet],
    ) -> None:
        result = json.loads(render_config_json(protocol_urls, "198.51.100.1", relay_entries=relay_entries))
        assert len(result["relays"]) == 1
        assert result["relays"][0]["name"] == "ru-moscow"
        assert result["relays"][0]["ip"] == "203.0.113.10"

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


# ---------------------------------------------------------------------------
# TestRenderSubscription
# ---------------------------------------------------------------------------


class TestRenderSubscription:
    def test_base64_encoded(self, protocol_urls: list[ProtocolURL]) -> None:
        result = render_subscription(protocol_urls)
        decoded = base64.b64decode(result)
        assert isinstance(decoded, bytes)

    def test_decodes_to_urls(self, protocol_urls: list[ProtocolURL]) -> None:
        result = render_subscription(protocol_urls)
        decoded = base64.b64decode(result).decode()
        lines = decoded.strip().split("\n")
        assert len(lines) == 2
        assert lines[0].startswith("vless://")
        assert lines[1].startswith("vless://")

    def test_includes_relay_urls(
        self,
        protocol_urls: list[ProtocolURL],
        relay_entries: list[RelayURLSet],
    ) -> None:
        result = render_subscription(protocol_urls, relay_entries=relay_entries)
        decoded = base64.b64decode(result).decode()
        lines = decoded.strip().split("\n")
        # 1 relay + 2 direct = 3
        assert len(lines) == 3
        assert "203.0.113.10" in lines[0]  # relay URL comes first

    def test_relay_urls_first(
        self,
        protocol_urls: list[ProtocolURL],
        relay_entries: list[RelayURLSet],
    ) -> None:
        result = render_subscription(protocol_urls, relay_entries=relay_entries)
        decoded = base64.b64decode(result).decode()
        lines = decoded.strip().split("\n")
        assert "203.0.113.10" in lines[0]

    def test_empty_urls(self) -> None:
        result = render_subscription([])
        assert result == ""

    def test_excludes_empty_urls(self) -> None:
        urls = [
            ProtocolURL(key="reality", label="Primary", url=REALITY_URL),
            ProtocolURL(key="wss", label="WSS", url=""),
        ]
        result = render_subscription(urls)
        decoded = base64.b64decode(result).decode()
        lines = decoded.strip().split("\n")
        assert len(lines) == 1


# ---------------------------------------------------------------------------
# TestRenderPWAShell
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# TestRenderManifest
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# TestConfigJsonSchema — protocol entry field validation (Gap #4)
# ---------------------------------------------------------------------------


class TestConfigJsonSchema:
    """Verify config.json protocol and relay entries have all required fields."""

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

    def test_relay_entry_structure(
        self,
        protocol_urls: list[ProtocolURL],
        relay_entries: list[RelayURLSet],
    ) -> None:
        result = json.loads(render_config_json(protocol_urls, "198.51.100.1", relay_entries=relay_entries))
        for relay in result["relays"]:
            assert "name" in relay
            assert "ip" in relay
            assert "urls" in relay
            assert isinstance(relay["urls"], list)
            assert len(relay["urls"]) >= 1

    def test_relay_url_entry_fields(
        self,
        protocol_urls: list[ProtocolURL],
        relay_entries: list[RelayURLSet],
    ) -> None:
        result = json.loads(render_config_json(protocol_urls, "198.51.100.1", relay_entries=relay_entries))
        required = {"key", "label", "url", "qr_b64"}
        for relay in result["relays"]:
            for url_entry in relay["urls"]:
                assert required.issubset(url_entry.keys()), f"Relay URL missing fields: {required - url_entry.keys()}"


# ---------------------------------------------------------------------------
# TestPWAAppsSync — _PWA_APPS vs apps.json (Gap #5)
# ---------------------------------------------------------------------------


class TestPWAAppsSync:
    """Verify _PWA_APPS in render.py matches website/src/data/apps.json."""

    def test_apps_match_apps_json(self) -> None:
        # Find apps.json relative to the repo root
        repo_root = Path(__file__).resolve().parent.parent
        apps_json_path = repo_root / "website" / "src" / "data" / "apps.json"
        assert apps_json_path.exists(), f"apps.json not found at {apps_json_path}"

        with open(apps_json_path) as f:
            apps_json = json.load(f)

        # Both should have the same entries (name + url pairs)
        json_pairs = {(a["name"], a["url"]) for a in apps_json}
        pwa_pairs = {(a["name"], a["url"]) for a in _PWA_APPS}
        assert json_pairs == pwa_pairs, (
            f"_PWA_APPS out of sync with apps.json.\n"
            f"In apps.json only: {json_pairs - pwa_pairs}\n"
            f"In _PWA_APPS only: {pwa_pairs - json_pairs}"
        )

    def test_apps_count_matches(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent
        apps_json_path = repo_root / "website" / "src" / "data" / "apps.json"
        with open(apps_json_path) as f:
            apps_json = json.load(f)
        assert len(_PWA_APPS) == len(apps_json)

    def test_deeplink_field_is_optional(self) -> None:
        """Apps without deep links must not be required to have the field."""
        for app in _PWA_APPS:
            assert "name" in app
            assert "url" in app
            # deeplink is optional — some apps have it, some don't

    def test_deeplink_templates_contain_url_placeholder(self) -> None:
        """Every deep link template must include a URL placeholder."""
        for app in _PWA_APPS:
            if "deeplink" in app:
                has_url = "{url}" in app["deeplink"] or "{url_raw}" in app["deeplink"] or "{url_b64}" in app["deeplink"]
                assert has_url, f"{app['name']} deeplink missing URL placeholder: {app['deeplink']}"

    def test_deeplink_sync_with_apps_json(self) -> None:
        """Deeplink values in _PWA_APPS must match apps.json."""
        repo_root = Path(__file__).resolve().parent.parent
        apps_json_path = repo_root / "website" / "src" / "data" / "apps.json"
        with open(apps_json_path) as f:
            apps_json = json.load(f)
        json_links = {a["name"]: a.get("deeplink") for a in apps_json}
        pwa_links = {a["name"]: a.get("deeplink") for a in _PWA_APPS}
        assert json_links == pwa_links, f"Deeplink mismatch:\n  apps.json: {json_links}\n  _PWA_APPS: {pwa_links}"

    def test_protocols_field_present(self) -> None:
        """Every app must have a protocols array."""
        for app in _PWA_APPS:
            assert "protocols" in app, f"{app['name']} missing protocols field"
            assert isinstance(app["protocols"], list), f"{app['name']} protocols must be a list"
            assert len(app["protocols"]) >= 1, f"{app['name']} protocols must not be empty"

    def test_protocols_values_valid(self) -> None:
        """Protocol values must be from the known set."""
        valid = {"reality", "xhttp", "wss"}
        for app in _PWA_APPS:
            for proto in app.get("protocols", []):
                assert proto in valid, f"{app['name']} has unknown protocol {proto!r}"

    def test_protocols_sync_with_apps_json(self) -> None:
        """Protocol values in _PWA_APPS must match apps.json."""
        repo_root = Path(__file__).resolve().parent.parent
        apps_json_path = repo_root / "website" / "src" / "data" / "apps.json"
        with open(apps_json_path) as f:
            apps_json = json.load(f)
        json_protocols = {a["name"]: a.get("protocols") for a in apps_json}
        pwa_protocols = {a["name"]: a.get("protocols") for a in _PWA_APPS}
        assert json_protocols == pwa_protocols, (
            f"Protocols mismatch:\n  apps.json: {json_protocols}\n  _PWA_APPS: {pwa_protocols}"
        )


# ---------------------------------------------------------------------------
# TestUnicodeClientNames — non-ASCII names (Gap #6)
# ---------------------------------------------------------------------------


class TestUnicodeClientNames:
    """Verify rendering works correctly with non-ASCII client names."""

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
        """Non-Latin characters should not corrupt JSON."""
        name = "\u0645\u0631\u06cc\u0645"  # Farsi: Maryam
        result = render_config_json(protocol_urls, "198.51.100.1", client_name=name)
        parsed = json.loads(result)
        assert parsed["client_name"] == name

    def test_subscription_unaffected_by_unicode_name(self, protocol_urls: list[ProtocolURL]) -> None:
        """Subscription rendering doesn't use client_name, but verify no crash."""
        result = render_subscription(protocol_urls)
        decoded = base64.b64decode(result).decode()
        assert "vless://" in decoded


class TestPWATemplateSource:
    def test_app_js_prefers_canonical_subscription_url(self) -> None:
        app_js = Path("src/meridian/templates/pwa/app.js").read_text()
        assert "config.subscription_url" in app_js

    def test_app_js_has_no_external_ping_dependency(self) -> None:
        app_js = Path("src/meridian/templates/pwa/app.js").read_text()
        assert "getmeridian.org/ping" not in app_js
