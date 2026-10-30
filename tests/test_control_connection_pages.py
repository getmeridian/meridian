"""Connection pages use live service state and never need an SSH publication."""

from __future__ import annotations

import base64
import re
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from meridian.cluster import ClusterConfig
from meridian.control.client import ControlClient
from meridian.control.models import (
    SUB_TOKEN_PATTERN,
    ClientEndpoint,
    PrivacySettings,
    ServiceSettings,
    UserCreate,
    UserRecord,
    UserUpdate,
)
from meridian.control.service import ControlService
from meridian.control.ui_views import share_view
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID, serve_control


@pytest.fixture
def page_fleet(tmp_path: Path) -> Iterator[tuple[str, ControlClient, UserRecord]]:
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as client:
        client.put_settings(ServiceSettings(profile_title="Meridian", public_url="https://198.51.100.1/connect"))
        person = client.put_user("guest")
        client.put_endpoint(
            "main",
            ClientEndpoint(
                remark="NL · Reality",
                protocol="reality",
                address="198.51.100.2",
                port=443,
                position=0,
                reality_sni="www.example.com",
                reality_public_key="pOCSkrZRwni5dyxWn1-puxPZBrRqtoyd-dwrRAn4ogk",
                reality_short_id="a1a1a1a1",
                fingerprint="chrome",
            ),
        )
        yield url, client, person


def test_browser_gets_html_and_apps_keep_configs(page_fleet: tuple[str, ControlClient, UserRecord]) -> None:
    url, _, person = page_fleet
    browser = httpx.get(f"{url}/sub/{person.sub_token}", headers={"Accept": "text/html"})
    assert browser.status_code == 200
    assert browser.headers["content-type"].startswith("text/html")
    assert "<!DOCTYPE html>" in browser.text
    assert browser.headers["cache-control"] == "no-store"
    assert "script-src 'self'" in browser.headers["content-security-policy"]
    assert "unsafe-inline" not in browser.headers["content-security-policy"]
    assert browser.headers["x-content-type-options"] == "nosniff"
    assert browser.headers["x-frame-options"] == "DENY"
    assert "noindex" in browser.headers["x-robots-tag"]
    assert "referrer-policy" not in browser.headers
    assert "content-encoding" not in browser.headers
    app = httpx.get(f"{url}/sub/{person.sub_token}", headers={"User-Agent": "Throne"})
    assert app.status_code == 200
    assert base64.b64decode(app.text).decode().startswith("vless://")
    assert person.uuid in base64.b64decode(app.text).decode()
    info = httpx.get(f"{url}/sub/{person.sub_token}/info", headers={"Accept": "text/html"})
    assert info.headers["content-type"].startswith("application/json")
    assert info.json()["user"]["username"] == "guest"


def test_browser_pages_and_info_never_register_devices(page_fleet: tuple[str, ControlClient, UserRecord]) -> None:
    url, client, person = page_fleet
    client.set_privacy(PrivacySettings(device_limits="full"))
    headers = {"Accept": "text/html", "x-hwid": "page-device-0001"}
    for suffix in ("", "/", "/info", "/json", "/index.html", "/config.json", "/manifest.webmanifest"):
        assert httpx.get(f"{url}/sub/{person.sub_token}{suffix}", headers=headers).status_code == 200
    app_headers = {"User-Agent": "Throne", "x-hwid": "page-device-0001"}
    assert httpx.get(f"{url}/sub/{person.sub_token}/info", headers=app_headers).status_code == 200
    assert client.user_devices(person.username).count == 0
    assert httpx.get(f"{url}/sub/{person.sub_token}", headers=app_headers).status_code == 200
    assert client.user_devices(person.username).count == 1


def test_people_token_invitees_get_live_pages(page_fleet: tuple[str, ControlClient, UserRecord]) -> None:
    url, admin, _ = page_fleet
    token = admin.create_token("People desk", "people")
    with ControlClient(url, token.token, report_mutations=False) as people:
        person = people.create_user(UserCreate(username="invitee"))
        page_url = f"{url}/sub/{person.sub_token}"
        assert httpx.get(page_url, headers={"Accept": "text/html"}).status_code == 200
        config = httpx.get(page_url + "/config.json").json()
        assert config["subscription_url"] == f"https://198.51.100.1/connect/sub/{person.sub_token}"
        people.update_user(person.username, UserUpdate(status="disabled"))
        assert httpx.get(page_url + "/info").json()["user"]["userStatus"] == "DISABLED"
        assert httpx.get(page_url, headers={"Accept": "text/html"}).status_code == 200
        reissued = people.reissue_user(person.username)
        assert httpx.get(page_url, headers={"Accept": "text/html"}).status_code == 404
        assert httpx.get(f"{url}/sub/{reissued.sub_token}", headers={"Accept": "text/html"}).status_code == 200


def test_person_data_is_live_private_and_under_the_token(page_fleet: tuple[str, ControlClient, UserRecord]) -> None:
    url, client, person = page_fleet
    config = httpx.get(f"{url}/sub/{person.sub_token}/config.json")
    assert config.status_code == 200
    assert config.headers["cache-control"] == "no-store"
    assert "noindex" in config.headers["x-robots-tag"]
    assert config.json()["subscription_url"] == f"https://198.51.100.1/connect/sub/{person.sub_token}"
    links = base64.b64decode(httpx.get(f"{url}/sub/{person.sub_token}").text).decode().splitlines()
    assert [entry["url"] for entry in config.json()["protocols"]] == links
    client.delete_endpoint("main")
    updated = httpx.get(f"{url}/sub/{person.sub_token}/config.json")
    assert updated.json()["protocols"] != config.json()["protocols"]
    manifest = httpx.get(f"{url}/sub/{person.sub_token}/manifest.webmanifest")
    assert manifest.status_code == 200
    assert manifest.headers["cache-control"] == "no-store"
    assert manifest.json()["start_url"] == f"/connect/sub/{person.sub_token}"


def test_assets_need_no_token_and_use_content_versions(page_fleet: tuple[str, ControlClient, UserRecord]) -> None:
    url, _, person = page_fleet
    assert re.fullmatch(SUB_TOKEN_PATTERN, "~pwa") is None
    worker = httpx.get(f"{url}/sub/~pwa/sw.js")
    assert worker.status_code == 200
    assert worker.headers["cache-control"] == "no-cache"
    version = re.search(r"CACHE_VERSION = '(pwa-[0-9a-f]+)'", worker.text)
    assert version is not None
    names = ("app.js", "account.js", "selfcheck.js", "support.html", "styles.css", "icon.svg", "apps.json", "probe.bin")
    for name in names:
        asset = httpx.get(f"{url}/sub/~pwa/{version[1]}/{name}")
        assert asset.status_code == 200, name
        if name == "support.html":
            assert asset.headers["cache-control"] == "no-store"
        else:
            assert "immutable" in asset.headers["cache-control"]
        assert person.sub_token not in asset.text
    browser = httpx.get(f"{url}/sub/{person.sub_token}", headers={"Accept": "text/html"})
    assert f"/connect/sub/~pwa/{version[1]}/app.js" in browser.text
    assert f"/connect/sub/{person.sub_token}/config.json" in browser.text


def test_legacy_uuid_redirects_and_reissue_revokes_old_links(page_fleet: tuple[str, ControlClient, UserRecord]) -> None:
    url, client, person = page_fleet
    legacy = httpx.get(f"{url}/sub/~legacy/{person.uuid.upper()}/")
    assert legacy.status_code == 302
    assert legacy.headers["location"] == f"/connect/sub/{person.sub_token}"
    assert legacy.headers["cache-control"] == "no-store"
    client.reissue_user("guest")
    for path in (f"/sub/{person.sub_token}", f"/sub/{person.sub_token}/config.json", f"/sub/~legacy/{person.uuid}/"):
        missing = httpx.get(url + path)
        assert missing.status_code == 404
        assert missing.json()["isFound"] is False


def test_old_worker_update_url_retires_cached_pages(page_fleet: tuple[str, ControlClient, UserRecord]) -> None:
    url, _, _ = page_fleet
    worker = httpx.get(f"{url}/sub/~legacy/pwa/sw.js")
    assert worker.status_code == 200
    assert worker.headers["content-type"].startswith("text/javascript")
    assert worker.headers["cache-control"] == "no-cache"
    assert "unregister" in worker.text
    assert "navigate" in worker.text


def test_console_people_share_the_subscription_as_the_page(tmp_path: Path) -> None:
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    try:
        service.put_settings(ServiceSettings(profile_title="Meridian", public_url="https://198.51.100.1/connect"))
        service.create_user(UserCreate(username="guest"))
        share = share_view(service, "guest")
        assert share.declared is False
        assert share.page_url == share.subscription_url != ""
    finally:
        service.close()


def test_old_page_markers_load_but_disappear_on_save(tmp_path: Path) -> None:
    path = tmp_path / "cluster.yml"
    path.write_text("version: 4\nconnection_page_markers:\n  - " + "a" * 64 + "\nfuture_setting: kept\n")
    cluster = ClusterConfig.load(path)
    assert cluster.validate() == []
    cluster.save(path)
    assert "connection_page_markers" not in path.read_text()
    assert "future_setting: kept" in path.read_text()
