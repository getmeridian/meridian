"""IPv6 endpoint input stays bare in documents and brackets once in share URIs."""

from __future__ import annotations

import json
from urllib.parse import urlsplit

import pytest
import yaml
from pydantic import ValidationError

from meridian.control.formats.entries import SubscriptionUser
from meridian.control.formats.links import share_uri
from meridian.control.formats.selector import DocumentFormat
from meridian.control.models import ClientEndpoint
from meridian.control.subscriptions import render_subscription
from meridian.core.topology import ProtocolKind
from tests.test_control_subscription_corpus import SHORT_ID, UUID
from tests.test_control_subscription_formats import DOMAIN_FLEET


@pytest.mark.parametrize("protocol", ["reality", "xhttp", "wss", "hysteria2"])
@pytest.mark.parametrize("address", ["2001:db8::1", "[2001:db8::1]"])
def test_ipv6_client_endpoints_bracket_uri_authorities_once(protocol: ProtocolKind, address: str) -> None:
    prototype = next(endpoint for endpoint in DOMAIN_FLEET.endpoints if endpoint.protocol == protocol)
    endpoint = ClientEndpoint.model_validate(prototype.model_dump() | {"address": address})

    uri = share_uri(endpoint, UUID)

    assert endpoint.address == "2001:db8::1"
    assert endpoint.model_dump()["address"] == "2001:db8::1"
    assert urlsplit(uri).hostname == "2001:db8::1"
    assert urlsplit(uri).netloc == f"{UUID}@[2001:db8::1]:443"


@pytest.mark.parametrize("document", ["xray-json", "mihomo", "stash", "singbox"])
def test_structured_documents_keep_ipv6_server_addresses_bare(document: DocumentFormat) -> None:
    prototype = DOMAIN_FLEET.endpoints[0]
    endpoint = ClientEndpoint.model_validate(prototype.model_dump() | {"address": "[2001:db8::1]"})
    person = SubscriptionUser(username="person", uuid=UUID, short_id=SHORT_ID)
    response = render_subscription(person, [endpoint], document, profile_title="Meridian")
    if document == "xray-json":
        server = json.loads(response.body)[0]["outbounds"][0]["settings"]["vnext"][0]["address"]
    elif document == "singbox":
        server = json.loads(response.body)["outbounds"][3]["server"]
    else:
        server = yaml.safe_load(response.body)["proxies"][0]["server"]
    assert server == "2001:db8::1"


@pytest.mark.parametrize(
    "address",
    ["[2001:db8::1", "2001:db8::1]", "[[2001:db8::1]]", "[not-an-address]", "[198.51.100.1]"],
)
def test_endpoint_brackets_must_enclose_one_valid_ipv6_address(address: str) -> None:
    with pytest.raises(ValidationError):
        ClientEndpoint.model_validate(DOMAIN_FLEET.endpoints[0].model_dump() | {"address": address})
