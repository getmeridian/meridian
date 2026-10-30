"""One inert update entry for an outdated or abandoned app: last, per request, never stored."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import unquote, urlsplit

import pytest

from meridian.control.formats.entries import NO_CONNECTIONS_REMARKS, SubscriptionUser, UserStatus
from meridian.control.formats.selector import DocumentFormat
from meridian.control.formats.stale_clients import STALE_REMARKS, stale_client
from meridian.control.models import ENDPOINT_REMARK_MAX_LENGTH, ClientEndpoint, PrivacySettings, UserUpdate
from meridian.control.subscriptions import DEVICE_LIMIT_REMARKS, SubscriptionResponse, render_subscription
from meridian.core.languages import LANGUAGES, Language
from meridian.core.subscription_preview import StaleClient
from tests.systemlab.subscription_client import (
    parse_base64_subscription,
    parse_mihomo_subscription,
    parse_singbox_subscription,
)
from tests.test_control_format_preview import DOMAIN_FLEET
from tests.test_control_subscription_corpus import FLEETS, SHORT_ID, UUID
from tests.test_control_ui_http import ADMIN_TOKEN, Fleet
from tests.test_control_ui_http import fleet as fleet

__all__ = ["fleet"]

DOCUMENTS: tuple[DocumentFormat, ...] = ("base64", "xray-json", "mihomo", "stash", "singbox")
OUTDATED = STALE_REMARKS["en"]["outdated"]
ABANDONED = STALE_REMARKS["en"]["abandoned"]


@pytest.mark.parametrize(
    ("user_agent", "kind"),
    [
        ("v2rayNG/1.9.16", "outdated"),
        ("v2rayNG/1.8.5", "outdated"),
        ("v2rayNG/1.9", "outdated"),
        ("v2rayng/1.9.0", "outdated"),
        ("v2rayNG/1.9.17", None),
        ("v2rayNG/1.10.16", None),
        ("v2rayNG/2.3.14", None),
        ("NekoBox/PC/4.0.1 (Prefer ClashMeta Format)", "abandoned"),
        ("NekoBox/Android/1.3.9 (Prefer ClashMeta Format)", None),
        ("Throne/1.0.13", None),
        ("Happ/3.2.1", None),
        ("curl/8.5.0", None),
        ("", None),
        (None, None),
    ],
    ids=lambda value: str(value),
)
def test_only_documented_outdated_or_abandoned_apps_earn_the_nudge(user_agent: str | None, kind: str | None) -> None:
    assert stale_client(user_agent) == kind


def test_every_nudge_fits_every_format_name_bound_in_all_four_languages() -> None:
    assert set(STALE_REMARKS) == set(LANGUAGES)
    for remarks in STALE_REMARKS.values():
        assert set(remarks) == {"outdated", "abandoned"}
        assert all(0 < len(remark) <= ENDPOINT_REMARK_MAX_LENGTH for remark in remarks.values())
        assert "NekoRay" in remarks["abandoned"] and "Throne" in remarks["abandoned"]


def _render(
    document: DocumentFormat,
    kind: StaleClient | None,
    *,
    fleet_name: str = "fleet",
    status: UserStatus = "active",
    language: Language = "en",
    endpoints: list[ClientEndpoint] | None = None,
    placeholders: tuple[str, ...] | None = None,
) -> SubscriptionResponse:
    corpus = FLEETS[fleet_name]
    user = SubscriptionUser(username="person", uuid=UUID, short_id=SHORT_ID, status=status, language=language)
    return render_subscription(
        user,
        corpus.endpoints if endpoints is None else endpoints,
        document,
        profile_title="Meridian",
        public_url=corpus.public_url,
        stale_client=kind,
        placeholder_remarks=placeholders,
    )


def _entries(document: DocumentFormat, response: SubscriptionResponse) -> list[str]:
    """The entry names a client lists, in document order."""
    if document == "base64":
        return [unquote(urlsplit(line).fragment) for line in parse_base64_subscription(response.body)]
    if document == "xray-json":
        return [config["remarks"] for config in json.loads(response.body)]
    if document == "singbox":
        return [outbound["tag"] for outbound in parse_singbox_subscription(response.body)["outbounds"][3:]]
    return [proxy["name"] for proxy in parse_mihomo_subscription(response.body)["proxies"]]


@pytest.mark.parametrize("fleet_name", ["fleet", "single"])
@pytest.mark.parametrize("document", DOCUMENTS)
@pytest.mark.parametrize("kind", ["outdated", "abandoned"])
def test_the_nudge_is_one_inert_entry_after_unchanged_real_entries(
    document: DocumentFormat, kind: StaleClient, fleet_name: str
) -> None:
    plain = _render(document, None, fleet_name=fleet_name)
    nudged = _render(document, kind, fleet_name=fleet_name)
    before = _entries(document, plain)
    after = _entries(document, nudged)
    assert after == [*before, STALE_REMARKS["en"][kind]]
    assert nudged.headers == plain.headers and nudged.content_type == plain.content_type
    if document == "base64":
        assert parse_base64_subscription(nudged.body)[:-1] == parse_base64_subscription(plain.body)
        assert "@0.0.0.0:1?" in parse_base64_subscription(nudged.body)[-1]
    elif document == "xray-json":
        configs, extra = json.loads(nudged.body), json.loads(plain.body)
        assert configs[:-1] == extra
        assert not [o for o in configs[-1]["outbounds"] if o["tag"].startswith("MERIDIAN_PROXY")]
    else:
        _groups_keep_real_entries_and_exclude_the_nudge(document, plain, nudged, STALE_REMARKS["en"][kind])


def _groups_keep_real_entries_and_exclude_the_nudge(
    document: DocumentFormat, plain: SubscriptionResponse, nudged: SubscriptionResponse, remark: str
) -> None:
    if document == "singbox":
        a, b = parse_singbox_subscription(plain.body), parse_singbox_subscription(nudged.body)
        groups = {o["tag"]: (o, next(g for g in b["outbounds"] if g["tag"] == o["tag"])) for o in a["outbounds"][:3]}
        assert b["outbounds"][3 : 3 + len(a["outbounds"]) - 3] == a["outbounds"][3:]
        proxy = b["outbounds"][-1]
        assert (proxy["server"], proxy["server_port"]) == ("0.0.0.0", 1)
        for tag, (old, new) in groups.items():
            listed = [name for name in new.get("outbounds", []) if name == remark]
            assert (listed == [remark]) == (new["type"] == "selector"), tag
            assert [n for n in new.get("outbounds", []) if n != remark] == old.get("outbounds", [])
        return
    a, b = parse_mihomo_subscription(plain.body), parse_mihomo_subscription(nudged.body)
    assert b["proxies"][:-1] == a["proxies"]
    assert (b["proxies"][-1]["server"], b["proxies"][-1]["port"]) == ("0.0.0.0", 1)
    for old, new in zip(a["proxy-groups"], b["proxy-groups"], strict=True):
        assert new["proxies"] == (old["proxies"] + [remark] if old["name"] == "Meridian" else old["proxies"])
        assert {k: v for k, v in new.items() if k != "proxies"} == {k: v for k, v in old.items() if k != "proxies"}


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("document", ["base64", "mihomo"])
def test_the_nudge_follows_the_persons_resolved_language(document: DocumentFormat, language: Language) -> None:
    names = _entries(document, _render(document, "abandoned", language=language))
    assert names[-1] == STALE_REMARKS[language]["abandoned"]


@pytest.mark.parametrize("status", ["disabled", "limited", "expired"])
@pytest.mark.parametrize("document", DOCUMENTS)
def test_a_suspended_person_sees_only_the_status_entries(document: DocumentFormat, status: UserStatus) -> None:
    assert _render(document, "outdated", status=status).body == _render(document, None, status=status).body


@pytest.mark.parametrize("document", DOCUMENTS)
def test_availability_and_device_limit_placeholders_replace_the_nudge_too(document: DocumentFormat) -> None:
    assert _render(document, "outdated", endpoints=[]).body == _render(document, None, endpoints=[]).body
    assert _entries(document, _render(document, "outdated", endpoints=[]))[0] == NO_CONNECTIONS_REMARKS[0]
    limited = _render(document, "outdated", placeholders=DEVICE_LIMIT_REMARKS)
    assert limited.body == _render(document, None, placeholders=DEVICE_LIMIT_REMARKS).body


@pytest.mark.parametrize("document", DOCUMENTS)
def test_a_nudge_named_like_an_existing_entry_is_dropped_not_duplicated(document: DocumentFormat) -> None:
    prototype = FLEETS["single"].endpoints[0]
    clash = prototype.model_copy(update={"remark": OUTDATED, "position": 0})
    plain = _render(document, None, endpoints=[clash])
    assert _render(document, "outdated", endpoints=[clash]).body == plain.body


def _fetch(fleet: Fleet, user_agent: str, token: str, accept: str = "*/*") -> Any:
    return fleet.call("GET", f"/sub/{token}", headers={"User-Agent": user_agent, "Accept": accept})


def test_live_delivery_adds_the_nudge_to_the_apps_document_and_never_to_the_page_or_info(fleet: Fleet) -> None:
    user = fleet.service.put_user("person")
    for index, endpoint in enumerate(DOMAIN_FLEET.endpoints):
        fleet.service.put_endpoint(f"entry-{index}", endpoint)
    old = fleet.service.subscription(user.sub_token, user_agent="NekoBox/PC/4.0.1 (Prefer ClashMeta Format)")
    current = fleet.service.subscription(user.sub_token, user_agent="Throne/1.0.13")
    assert _entries("base64", old)[-1] == ABANDONED
    assert _entries("base64", old)[:-1] == _entries("base64", current)
    info = fleet.service.subscription(user.sub_token, "info", user_agent="v2rayNG/1.8.5")
    assert ABANDONED not in info.body and OUTDATED not in info.body
    page = fleet.service.subscription(user.sub_token, user_agent="v2rayNG/1.8.5", accept="text/html")
    assert OUTDATED not in page.body
    explicit = fleet.service.subscription(user.sub_token, "mihomo", user_agent="v2rayNG/1.8.5")
    assert _entries("mihomo", explicit)[-1] == OUTDATED


@pytest.mark.parametrize("level", ["off", "private", "full"])
def test_the_nudge_stores_nothing_and_leaves_device_limits_as_they_were(fleet: Fleet, level: str) -> None:
    user = fleet.service.put_user("person")
    fleet.service.put_endpoint("entry", DOMAIN_FLEET.endpoints[0])
    fleet.service.set_privacy(PrivacySettings(device_limits=level), author="root")  # type: ignore[arg-type]
    headers = {"x-hwid": "device-00000001", "x-device-os": "Android"}
    db = fleet.service._db
    modern = fleet.service.subscription(user.sub_token, user_agent="v2rayNG/1.10.16", device_headers=headers)
    before = db.total_changes
    again = fleet.service.subscription(user.sub_token, user_agent="v2rayNG/1.10.16", device_headers=headers)
    repeat_changes = db.total_changes - before
    before = db.total_changes
    stale = fleet.service.subscription(user.sub_token, user_agent="v2rayNG/1.8.5", device_headers=headers)
    assert db.total_changes - before == repeat_changes
    assert stale.headers == again.headers == modern.headers
    assert _entries("xray-json", stale)[:-1] == _entries("xray-json", modern)
    assert "1.8.5" not in "\n".join(db.iterdump())


@pytest.mark.parametrize("status", ["active", "disabled"])
def test_preview_explains_the_nudge_as_its_own_reason_without_the_user_agent(fleet: Fleet, status: str) -> None:
    fleet.service.put_user("person")
    for index, endpoint in enumerate(DOMAIN_FLEET.endpoints):
        fleet.service.put_endpoint(f"entry-{index}", endpoint)
    if status == "disabled":
        fleet.service.update_user("person", UserUpdate(status="disabled"))
    agent = "v2rayNG/1.8.5"
    reply = fleet.call(
        "POST", "/api/subscription/preview", {"username": "person", "user_agent": agent}, bearer=ADMIN_TOKEN
    )
    assert reply.status == 200 and reply.body["format"] == "xray-json"
    assert reply.body["stale_client"] == ("outdated" if status == "active" else None)
    assert agent not in json.dumps(reply.body)
    assert (reply.body["placeholder"] is None) == (status == "active")
    for other in ("v2rayNG/1.10.16", "Happ/1"):
        reply = fleet.call(
            "POST", "/api/subscription/preview", {"username": "person", "user_agent": other}, bearer=ADMIN_TOKEN
        )
        assert reply.body["stale_client"] is None
    page = fleet.call(
        "POST",
        "/api/subscription/preview",
        {"username": "person", "user_agent": agent, "accept": "text/html"},
        bearer=ADMIN_TOKEN,
    )
    assert page.body["stale_client"] is None
