"""Reviewed fleet contact: canonical links, atomic settings, and public delivery."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import unquote, urlsplit

import httpx
import pytest
import yaml
from pydantic import ValidationError

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.compiler import compile_topology
from meridian.compiler.models import ServiceSettingsPayload
from meridian.control.client import ControlClient
from meridian.control.formats.entries import SubscriptionUser
from meridian.control.formats.status_remarks import STATUS_REMARKS
from meridian.control.models import ENDPOINT_REMARK_MAX_LENGTH, ServiceSettings
from meridian.control.service import ControlService
from meridian.control.subscriptions import render_subscription
from meridian.core.defaults import SUPPORT_PAGE
from meridian.core.fleet_document import intent_changes
from meridian.core.topology import DeliveryIntent
from meridian.pwa import load_pwa_assets
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID, serve_control
from tests.support.topology import single_exit_intent

CONTACT = {"text": "Help: «连接» & support", "link": "tg://resolve?domain=sample_support"}


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        (" HTTPS://EXAMPLE.COM:443/help ", "https://example.com/help"),
        ("https://пример.example/帮助", "https://xn--e1afmkfd.example/%E5%B8%AE%E5%8A%A9"),
        ("TG:resolve?domain=sample_support", "tg://resolve?domain=sample_support"),
        ("mailto:help@EXAMPLE.COM?subject=Connection%20help", "mailto:help@example.com?subject=Connection%20help"),
    ],
    ids=["https", "unicode-url", "telegram", "mail"],
)
def test_reviewed_contact_links_are_canonical_and_ascii(raw: str, canonical: str) -> None:
    delivery = DeliveryIntent.model_validate({"owner_contact": {"text": "Ask for help", "link": raw}})
    assert delivery.owner_contact.link == canonical
    assert delivery.owner_contact.link.isascii()


@pytest.mark.parametrize(
    "contact",
    [
        {"text": "x" * 121},
        {"text": "two\nlines"},
        {"link": "http://example.com/help"},
        {"link": "javascript:alert(1)"},
        {"link": "data:text/html,hello"},
        {"link": "//example.com/help"},
        {"link": "https://user:password@example.com/help"},
        {"link": "mailto:help@example.com?subject=Hi%0ABcc:test"},
        {"link": "tg:"},
        {"link": "https://example.com:70000/"},
    ],
)
def test_contact_refuses_unsafe_links_and_non_plain_text(contact: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        DeliveryIntent.model_validate({"owner_contact": contact})


def test_empty_contact_preserves_existing_serialization() -> None:
    assert "owner_contact" not in DeliveryIntent().model_dump(mode="json")
    settings = ServiceSettings(profile_title="Meridian")
    assert "owner_contact" not in settings.model_dump(mode="json")


def test_contact_travels_through_compiler_and_fleet_document() -> None:
    current = single_exit_intent()
    delivery = DeliveryIntent.model_validate({"owner_contact": CONTACT, "update_interval_hours": 24})
    updated = single_exit_intent(delivery=delivery)
    plan = compile_topology(updated)
    payload = next(
        resource.payload for resource in plan.resources if isinstance(resource.payload, ServiceSettingsPayload)
    )
    assert payload.owner_contact == delivery.owner_contact
    assert payload.update_interval_hours == 24
    changes = intent_changes(current, updated)
    assert any(change.subject == "delivery" and change.field == "owner_contact" for change in changes)
    assert "owner_contact" in updated.model_dump()["delivery"]


def test_owner_contact_and_delivery_settings_roll_back_together(tmp_path: Path) -> None:
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    before = service.settings()
    updated = ServiceSettings.model_validate(
        {
            "profile_title": "New title",
            "public_url": "https://198.51.100.1/connect",
            "update_interval_hours": 24,
            "client_routing": "full",
            "owner_contact": CONTACT,
        }
    )
    original_write = service._store.set_meta

    def fail_after_page_write(key: str, value: str) -> None:
        original_write(key, value)
        if key == "connection_page_settings":
            raise RuntimeError("interrupted write")

    try:
        with patch.object(service._store, "set_meta", side_effect=fail_after_page_write):
            with pytest.raises(RuntimeError, match="interrupted write"):
                service.put_settings(updated)
        assert service.settings() == before
        assert service.put_settings(updated) == updated
        assert service.settings() == updated
    finally:
        service.close()


def test_service_settings_driver_carries_exactly_the_reviewed_contact(tmp_path: Path) -> None:
    from meridian.reconciler.meridian_drivers import MeridianDriverContext, ServiceSettingsDriver
    from meridian.reconciler.resources import build_resource_actions

    delivery = DeliveryIntent.model_validate({"owner_contact": CONTACT})
    with serve_control(tmp_path) as url:
        cluster = ClusterConfig(
            panel=PanelConfig(url=url, api_token=ADMIN_TOKEN, server_ip="198.51.100.1"),
            topology_intent=single_exit_intent(delivery=delivery),
        )
        plan = compile_topology(cluster.topology_intent)
        action = next(
            action
            for action in build_resource_actions(plan, 1)
            if isinstance(action.resource.payload, ServiceSettingsPayload)
        )
        context = MagicMock(spec=MeridianDriverContext)
        context.cluster = cluster
        context.client = lambda: ControlClient(url, ADMIN_TOKEN, report_mutations=False)
        driver = ServiceSettingsDriver(context)
        driver.apply(action, None)
        with ControlClient(url, ADMIN_TOKEN, report_mutations=False) as client:
            assert client.settings().owner_contact == delivery.owner_contact


def test_contact_is_live_on_pages_and_overrides_only_the_support_header(tmp_path: Path) -> None:
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as client:
        settings = ServiceSettings.model_validate(
            {
                "profile_title": "Meridian",
                "public_url": "https://198.51.100.1/connect",
                "owner_contact": CONTACT,
            }
        )
        client.put_settings(settings)
        person = client.put_user("sample")
        response = httpx.get(f"{url}/sub/{person.sub_token}")
        assert response.headers["support-url"] == CONTACT["link"]
        assert response.headers["profile-web-page-url"] == f"{settings.public_url}/sub/{person.sub_token}"
        config = httpx.get(f"{url}/sub/{person.sub_token}/config.json").json()
        assert config["owner_contact"] == CONTACT
        version = load_pwa_assets().version
        support = httpx.get(f"{url}/sub/~pwa/{version}/support.html")
        assert support.status_code == 200
        assert support.headers["cache-control"] == "no-store"
        assert "sample_support" in support.text and "«连接» &amp; support" in support.text
        assert person.sub_token not in support.text
        client.put_settings(ServiceSettings(profile_title="Meridian", public_url=settings.public_url))
        cleared = httpx.get(f"{url}/sub/{person.sub_token}")
        assert cleared.headers["support-url"] == f"{settings.public_url}/{SUPPORT_PAGE}#{person.sub_token}"
        assert "owner_contact" not in httpx.get(f"{url}/sub/{person.sub_token}/config.json").json()
        assert "sample_support" not in httpx.get(f"{url}/sub/~pwa/{version}/support.html").text


@pytest.mark.parametrize("status", ["disabled", "expired", "limited"])
@pytest.mark.parametrize("document", ["base64", "xray-json", "mihomo", "stash", "singbox", "browser"])
def test_short_contact_remarks_stay_bounded_and_every_format_parses(status: str, document: str) -> None:
    delivery = DeliveryIntent.model_validate({"owner_contact": CONTACT})
    user = SubscriptionUser(
        username="sample", uuid="550e8400-e29b-41d4-a716-446655440000", short_id="sampleShortUuid00", status=status
    )
    response = render_subscription(user, [], document, profile_title="Meridian", owner_contact=delivery.owner_contact)
    if document == "base64":
        links = base64.b64decode(response.body).decode().splitlines()
        names = [unquote(urlsplit(link).fragment) for link in links]
        assert all(urlsplit(link).port == 1 for link in links)
    elif document in ("mihomo", "stash"):
        config = yaml.safe_load(response.body)
        names = [proxy["name"] for proxy in config["proxies"]]
    elif document == "xray-json":
        names = [item["remarks"] for item in json.loads(response.body)]
    elif document == "singbox":
        names = [item["tag"] for item in json.loads(response.body)["outbounds"] if item["type"] == "vless"]
    else:
        names = [unquote(urlsplit(link).fragment) for link in json.loads(response.body)["links"]]
    assert names and all(CONTACT["text"] in name for name in names)
    assert all(len(name) <= ENDPOINT_REMARK_MAX_LENGTH for name in names)


def test_long_contact_text_leaves_status_placeholders_unchanged() -> None:
    delivery = DeliveryIntent.model_validate({"owner_contact": {"text": "x" * 120}})
    user = SubscriptionUser(
        username="sample", uuid="550e8400-e29b-41d4-a716-446655440000", short_id="sampleShortUuid00", status="expired"
    )
    response = render_subscription(user, [], "base64", profile_title="Meridian", owner_contact=delivery.owner_contact)
    names = tuple(unquote(urlsplit(link).fragment) for link in base64.b64decode(response.body).decode().splitlines())
    assert names == STATUS_REMARKS["en"]["expired"]


def test_public_contact_can_be_proposed_and_stored_without_becoming_a_secret(tmp_path: Path) -> None:
    from meridian.control.models import RevisionProposal
    from meridian.control.tokens import ROOT

    delivery = DeliveryIntent.model_validate(
        {"owner_contact": {"text": "Ask for help", "link": "https://example.com/help"}}
    )
    intent = single_exit_intent(delivery=delivery).model_dump(mode="json")
    service = ControlService(tmp_path / "control.db", fleet_id=FLEET_ID, version="4.0.0")
    try:
        proposal = RevisionProposal(intent=intent, plan_hash="a" * 64, review_hash="b" * 64)
        revision = service.revisions.propose(proposal, caller=ROOT)
        assert revision.intent["delivery"]["owner_contact"]["link"] == "https://example.com/help"
    finally:
        service.close()


def test_revealing_a_public_contact_path_keeps_other_links_and_credentials_redacted() -> None:
    from meridian.core.redaction import REDACTED, redact

    result = redact(
        {
            "data": {
                "owner_contact": {"link": "https://example.com/help", "password": "private-value"},
                "other": {"link": "https://example.com/private-link"},
            }
        },
        reveal=frozenset({"data.owner_contact.link"}),
    )
    assert result["data"]["owner_contact"]["link"] == "https://example.com/help"
    assert result["data"]["owner_contact"]["password"] == REDACTED
    assert result["data"]["other"]["link"] == f"https://example.com/{REDACTED}"
