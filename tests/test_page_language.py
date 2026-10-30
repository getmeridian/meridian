"""Fleet and person defaults reach HTML before config or browser requests."""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest

from meridian.control.client import ControlClient
from meridian.control.models import UserRecord, UserUpdate
from meridian.control.service import ControlService
from meridian.core.languages import LanguageSettings
from meridian.core.subscription_preview import SubscriptionPreviewRequest
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID
from tests.test_control_connection_pages import page_fleet as page_fleet
from tests.test_pwa_runtime import page_data as page_data


def test_unset_new_and_existing_fleets_follow_the_browser(tmp_path: Path) -> None:
    path = tmp_path / "control.db"
    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    try:
        service.put_user("person")
        assert service.languages.read().language == "auto"
    finally:
        service.close()
    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    try:
        assert service.languages.read().language == "auto"
    finally:
        service.close()


def test_stored_english_remains_an_explicit_owner_choice(tmp_path: Path) -> None:
    path = tmp_path / "control.db"
    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    service.languages.set(LanguageSettings(language="ru"))
    service.languages.set(LanguageSettings(language="en"))
    service.close()
    service = ControlService(path, fleet_id=FLEET_ID, version="4.0.0")
    try:
        assert service.languages.read().language == "en"
    finally:
        service.close()


@pytest.mark.parametrize("language", ["auto", "en", "ru", "fa", "zh"])
def test_api_language_round_trip(page_fleet: tuple[str, ControlClient, UserRecord], language: str) -> None:
    url, client, _ = page_fleet
    response = httpx.put(
        f"{url}/api/language", json={"language": language}, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["language"] == client.language().language == language
    repeated = httpx.put(
        f"{url}/api/language", json={"language": language}, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}
    )
    assert repeated.status_code == 200 and repeated.json()["changed"] is False


@pytest.mark.parametrize(
    ("fleet_language", "person_language", "expected"),
    [
        ("auto", None, "auto"),
        ("en", None, "en"),
        ("ru", None, "ru"),
        ("fa", None, "fa"),
        ("zh", None, "zh"),
        ("ru", "en", "en"),
        ("zh", "fa", "fa"),
        ("auto", "ru", "ru"),
    ],
)
@pytest.mark.parametrize("filename", ["index.html", "support.html"])
def test_person_html_carries_resolved_language_and_blocking_bootstrap(
    page_fleet: tuple[str, ControlClient, UserRecord],
    fleet_language: str,
    person_language: str | None,
    expected: str,
    filename: str,
) -> None:
    url, client, person = page_fleet
    client.set_language(LanguageSettings(language=fleet_language))
    client.update_user(person.username, UserUpdate(language=person_language))
    response = httpx.get(f"{url}/sub/{person.sub_token}/{filename}")
    assert response.status_code == 200
    lang = "en" if expected == "auto" else expected
    direction = "rtl" if lang == "fa" else "ltr"
    assert f'<html lang="{lang}" dir="{direction}" data-language="{expected}"' in response.text
    head = response.text.split("</head>", 1)[0]
    bootstrap = re.search(r'<script\s+src="([^"]*/language\.js)"\s*></script>', head)
    assert bootstrap is not None, "language selection must run before the first paint"
    assert head.index(bootstrap[0]) < head.index('rel="stylesheet"')
    assert response.headers["cache-control"] == "no-store"
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert "unsafe-inline" not in response.headers["content-security-policy"]
    if filename == "support.html":
        assert f'data-page="/connect/sub/{person.sub_token}"' in response.text
        assert "/connect/sub/~pwa/" in bootstrap[1]


def test_support_header_points_to_person_html(page_fleet: tuple[str, ControlClient, UserRecord]) -> None:
    url, _, person = page_fleet
    response = httpx.get(f"{url}/sub/{person.sub_token}", headers={"User-Agent": "Throne"})
    assert response.headers["support-url"] == f"https://198.51.100.1/connect/sub/{person.sub_token}/support.html"


def test_shared_legacy_support_html_uses_live_fleet_language(page_fleet: tuple[str, ControlClient, UserRecord]) -> None:
    url, client, _ = page_fleet
    client.set_language(LanguageSettings(language="fa"))
    worker = httpx.get(f"{url}/sub/~pwa/sw.js")
    version = re.search(r"CACHE_VERSION = '(pwa-[0-9a-f]+)'", worker.text)
    assert version is not None
    response = httpx.get(f"{url}/sub/~pwa/{version[1]}/support.html")
    assert response.status_code == 200
    assert '<html lang="fa" dir="rtl" data-language="fa"' in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    ("language", "override", "expected"),
    [
        ("auto", None, "Subscription disabled"),
        ("auto", "ru", "Подписка отключена"),
        ("ru", None, "Подписка отключена"),
        ("ru", "fa", "اشتراک غیرفعال است"),
        ("zh", "en", "Subscription disabled"),
    ],
)
def test_status_remarks_use_person_then_fleet_and_english_for_auto(
    page_fleet: tuple[str, ControlClient, UserRecord],
    language: str,
    override: str | None,
    expected: str,
) -> None:
    url, client, person = page_fleet
    client.set_language(LanguageSettings(language=language))
    client.update_user(person.username, UserUpdate(status="disabled", language=override))
    response = httpx.get(f"{url}/sub/{person.sub_token}", headers={"User-Agent": "Throne", "Accept-Language": "fa,ru"})
    assert expected in unquote(base64.b64decode(response.text).decode())
    preview = client.preview_subscription(SubscriptionPreviewRequest(username=person.username, user_agent="Throne"))
    assert preview.status == "disabled"


@pytest.mark.skipif(shutil.which("node") is None, reason="page language checks need Node")
@pytest.mark.parametrize("page", ["connection", "support"])
@pytest.mark.parametrize(
    ("page_language", "saved", "browser", "expected", "storage_unavailable"),
    [
        ("fa", "en", "ru", "en", False),
        ("zh", "fa", "ru", "fa", False),
        ("en", None, "fa", "en", False),
        ("ru", None, "zh", "ru", False),
        ("fa", None, "en", "fa", False),
        ("zh", None, "ru", "zh", False),
        ("auto", None, "ru-RU", "ru", False),
        ("auto", None, "fa-IR", "fa", False),
        ("auto", None, "zh-CN", "zh", False),
        ("auto", None, "de-DE", "en", False),
        ("ru", "invalid", "fa", "ru", False),
        ("fa", "ru", "en", "fa", True),
    ],
)
def test_page_language_order_before_config_and_in_early_messages(
    page_data: dict[str, object],
    page: str,
    page_language: str,
    saved: str | None,
    browser: str,
    expected: str,
    storage_unavailable: bool,
) -> None:
    result = subprocess.run(
        ["node", "tests/page_language.cjs", page],
        input=json.dumps(
            page_data
            | {
                "page_language": page_language,
                "saved": saved,
                "browser": browser,
                "expected": expected,
                "storage_unavailable": storage_unavailable,
            }
        ),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_clearing_person_override_restores_fleet_page_default(
    page_fleet: tuple[str, ControlClient, UserRecord],
) -> None:
    url, client, person = page_fleet
    client.set_language(LanguageSettings(language="ru"))
    client.update_user(person.username, UserUpdate(language="fa"))
    client.update_user(person.username, UserUpdate(traffic_limit_bytes=4096))
    assert client.get_user(person.username).language == "fa"
    client.update_user(person.username, UserUpdate(language=None))
    for filename in ("index.html", "support.html"):
        assert 'data-language="ru"' in httpx.get(f"{url}/sub/{person.sub_token}/{filename}").text


def test_auto_is_only_a_fleet_choice(page_fleet: tuple[str, ControlClient, UserRecord]) -> None:
    url, _, person = page_fleet
    response = httpx.patch(
        f"{url}/api/users/{person.username}",
        json={"language": "auto"},
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
    )
    assert response.status_code == 400


@pytest.mark.skipif(shutil.which("node") is None, reason="page language checks need Node")
@pytest.mark.parametrize("mode", ["legacy-contact", "legacy-empty"])
def test_old_support_fragments_navigate_locally_without_a_config_fetch(page_data: dict[str, object], mode: str) -> None:
    result = subprocess.run(
        ["node", "tests/page_language.cjs", mode],
        input=json.dumps(
            page_data
            | {"page_language": "ru", "saved": "fa", "browser": "en", "expected": "fa", "storage_unavailable": False}
        ),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
