"""Control-service subscription documents beyond the corpus: client parsers, engines, statuses, headers.

Real engines are opt-in, like `MERIDIAN_TEST_XRAY`: `MERIDIAN_TEST_MIHOMO=/path/to/mihomo`
runs `mihomo -t` (with `MERIDIAN_TEST_MIHOMO_GEODATA=<dir>` holding GeoSite.dat and
Country.mmdb, or it downloads them) and `MERIDIAN_TEST_SINGBOX=/path/to/sing-box` runs
`sing-box check` on every Mihomo, Stash, and sing-box document rendered here.
"""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest
import yaml

from meridian.control.formats.entries import STATUS_REMARKS, SubscriptionUser, UserStatus
from meridian.control.formats.selector import DocumentFormat, select_format
from meridian.control.subscriptions import SubscriptionResponse, render_subscription
from meridian.xray_client import parse_xray_subscription, proxy_outbounds
from tests.systemlab.subscription_client import (
    MIHOMO_CLIENT_TYPE,
    SINGBOX_CLIENT_TYPE,
    check_with_engine,
    parse_mihomo_subscription,
    parse_singbox_subscription,
)
from tests.test_control_subscription_corpus import FLEETS, SHORT_ID, UUID

STATUSES: tuple[UserStatus, ...] = ("active", "disabled", "limited", "expired")
CASES = [(topology, status) for topology in FLEETS for status in STATUSES]
CASE_IDS = [f"{topology}-{status}" for topology, status in CASES]


def _render(
    topology: str, document: DocumentFormat, status: UserStatus = "active", **user: Any
) -> SubscriptionResponse:
    fleet = FLEETS[topology]
    person = SubscriptionUser(username=fleet.username, uuid=UUID, short_id=SHORT_ID, status=status, **user)
    return render_subscription(
        person, fleet.endpoints, document, profile_title=fleet.title, public_url=fleet.public_url
    )


@pytest.mark.parametrize(("topology", "status"), CASES, ids=CASE_IDS)
def test_documents_pass_the_client_parsers(topology: str, status: UserStatus) -> None:
    parse_mihomo_subscription(_render(topology, "mihomo", status).body)
    parse_mihomo_subscription(_render(topology, "stash", status).body)
    parse_singbox_subscription(_render(topology, "singbox", status).body)
    configs = json.loads(_render(topology, "xray-json", status).body)
    assert isinstance(configs, list) and configs
    if status == "active":
        primary = parse_xray_subscription(json.dumps(configs))
        assert proxy_outbounds(primary)


@pytest.mark.parametrize("status", ["disabled", "limited", "expired"])
def test_a_suspended_user_sees_why_in_every_format(status: UserStatus) -> None:
    remarks = list(STATUS_REMARKS[status])
    lines = base64.b64decode(_render("single", "base64", status).body).decode().splitlines()
    assert [unquote(line.rsplit("#", 1)[1]) for line in lines] == remarks
    assert all("@0.0.0.0:1?" in line and UUID not in line for line in lines)
    assert [config["remarks"] for config in json.loads(_render("single", "xray-json", status).body)] == remarks
    assert [proxy["name"] for proxy in yaml.safe_load(_render("single", "mihomo", status).body)["proxies"]] == remarks
    singbox = json.loads(_render("single", "singbox", status).body)
    assert [outbound["tag"] for outbound in singbox["outbounds"][3:]] == remarks
    info = json.loads(_render("single", "browser", status).body)
    assert info["user"]["userStatus"] == status.upper() and info["user"]["isActive"] is False


def test_userinfo_carries_traffic_limit_and_expiry() -> None:
    expiry = datetime(2027, 1, 2, 3, 4, 5, tzinfo=UTC)
    response = _render("single", "base64", expires_at=expiry, used_bytes=1536, limit_bytes=10 * 1024**3)
    assert response.headers["subscription-userinfo"] == (
        f"upload=0; download=1536; total={10 * 1024**3}; expire={int(expiry.timestamp())}"
    )
    info = json.loads(_render("single", "browser", expires_at=expiry, used_bytes=1536).body)["user"]
    assert (info["expiresAt"], info["trafficUsed"], info["trafficLimit"]) == (
        "2027-01-02T03:04:05.000Z",
        "1.50 KiB",
        "0",
    )


def test_links_from_the_public_url_are_left_out_without_one() -> None:
    fleet = FLEETS["single"]
    user = SubscriptionUser(username="ana maría", uuid=UUID, short_id=SHORT_ID)
    response = render_subscription(user, fleet.endpoints, "base64", profile_title="Meridian")
    assert "support-url" not in response.headers and "profile-web-page-url" not in response.headers
    assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''ana%20mar%C3%ADa"


@pytest.mark.parametrize(
    ("user_agent", "accept", "document"),
    [
        (None, None, "base64"),
        ("", "*/*", "base64"),
        ("   ", "*/*", "base64"),
        ("Mozilla/5.0", "text/html,*/*", "browser"),
        ("Happ/3.2.1", "*/*", "xray-json"),
        ("v2rayNG/1.10.16", "*/*", "xray-json"),
        ("v2rayNG", "*/*", "base64"),
        ("HiddifyNext/2.5.7 (android) like ClashMeta v2ray sing-box", "*/*", "singbox"),
        ("Stash/3.2.0 Clash/1.11.0", "*/*", "stash"),
        ("ClashForAndroid/2.5.12", "*/*", "mihomo"),
        ("clash.meta", "*/*", "mihomo"),
        ("Some app sfi/1.0", "*/*", "base64"),
    ],
)
def test_user_agent_rules_beyond_the_corpus(user_agent: str | None, accept: str | None, document: str) -> None:
    assert select_format(user_agent, accept) == document


def _engine(variable: str) -> Path:
    configured = os.environ.get(variable)
    if not configured:
        pytest.skip(f"set {variable} to check documents with the client's own engine")
    return Path(configured)


@pytest.mark.parametrize(("topology", "status"), CASES, ids=CASE_IDS)
def test_mihomo_accepts_the_documents(
    topology: str, status: UserStatus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = _engine("MERIDIAN_TEST_MIHOMO")
    monkeypatch.setenv("PATH", f"{binary.parent}{os.pathsep}{os.environ['PATH']}")
    geodata = os.environ.get("MERIDIAN_TEST_MIHOMO_GEODATA")
    documents: tuple[DocumentFormat, ...] = ("mihomo", "stash")
    for index, document in enumerate(documents):
        check_with_engine(
            MIHOMO_CLIENT_TYPE,
            _render(topology, document, status).body,
            tmp_path / str(index),
            geodata_dir=Path(geodata) if geodata else None,
        )


@pytest.mark.parametrize(("topology", "status"), CASES, ids=CASE_IDS)
def test_sing_box_accepts_the_documents(
    topology: str, status: UserStatus, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = _engine("MERIDIAN_TEST_SINGBOX")
    monkeypatch.setenv("PATH", f"{binary.parent}{os.pathsep}{os.environ['PATH']}")
    check_with_engine(SINGBOX_CLIENT_TYPE, _render(topology, "singbox", status).body, tmp_path)
