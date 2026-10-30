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
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import pytest
import yaml

from meridian.compiler.delivery import xray_json_template
from meridian.control.formats.entries import (
    NO_CONNECTIONS_REMARKS,
    STATUS_REMARKS,
    SubscriptionUser,
    UserStatus,
    presentation_endpoints,
    primaries,
)
from meridian.control.formats.selector import DocumentFormat, select_format
from meridian.control.formats.xray_json import xray_json_document
from meridian.control.models import ClientEndpoint
from meridian.control.subscriptions import SubscriptionResponse, render_subscription
from meridian.xray_client import parse_xray_subscription, proxy_outbounds
from tests.systemlab.subscription_client import (
    MIHOMO_CLIENT_TYPE,
    SINGBOX_CLIENT_TYPE,
    check_with_engine,
    parse_base64_subscription,
    parse_mihomo_subscription,
    parse_singbox_subscription,
)
from tests.test_control_subscription_corpus import EXIT_B, FLEETS, KEYS, SALAMANDER, SHORT_ID, UUID, Fleet

# Domain mode, which the corpus never captured: self-steal Reality on the domain, XHTTP and
# WSS dialing the domain (so a CDN may front them), and Hysteria2 dialing the server itself.
DOMAIN = "vpn.example.com"
WS_PATH = "w5j7k9m2n4p6"
_TLS = {"port": 443, "tls_sni": DOMAIN, "host": DOMAIN, "address": DOMAIN}
WSS = ClientEndpoint(remark="WebSocket · CDN", protocol="wss", position=10, fingerprint="chrome", path=WS_PATH, **_TLS)
DOMAIN_FLEET = Fleet(
    "solo",
    "Meridian",
    EXIT_B,
    [
        *(
            ClientEndpoint(
                remark=f"Reality · {label}",
                protocol="reality",
                address=EXIT_B,
                port=443,
                position=position,
                fingerprint=label.lower(),
                reality_sni=DOMAIN,
                reality_public_key=KEYS["exit-b"][0],
                reality_short_id=KEYS["exit-b"][1],
                fingerprint_copy=label != "Chrome",
            )
            for position, label in enumerate(("Chrome", "Firefox"))
        ),
        *(
            ClientEndpoint(
                remark=f"XHTTP · {label}",
                protocol="xhttp",
                position=6 + position,
                fingerprint=label.lower(),
                path="x9k2m4n6p8q1",
                fingerprint_copy=label != "Chrome",
                **_TLS,
            )
            for position, label in enumerate(("Chrome", "Firefox"))
        ),
        WSS,
        ClientEndpoint(
            remark="Hysteria2 · UDP",
            protocol="hysteria2",
            address=EXIT_B,
            port=443,
            position=12,
            tls_sni=DOMAIN,
            salamander_password=SALAMANDER,
        ),
    ],
)
ALL_FLEETS = {**FLEETS, "domain": DOMAIN_FLEET}

STATUSES: tuple[UserStatus, ...] = ("active", "disabled", "limited", "expired")
CASES = [(topology, status) for topology in ALL_FLEETS for status in STATUSES]
CASE_IDS = [f"{topology}-{status}" for topology, status in CASES]


def _render(
    topology: str, document: DocumentFormat, status: UserStatus = "active", **user: Any
) -> SubscriptionResponse:
    fleet = ALL_FLEETS[topology]
    person = SubscriptionUser(username=fleet.username, uuid=UUID, short_id=SHORT_ID, status=status, **user)
    return render_subscription(
        person, fleet.endpoints, document, profile_title=fleet.title, public_url=fleet.public_url
    )


def _render_endpoints(
    endpoints: list[ClientEndpoint], document: DocumentFormat, status: UserStatus = "active"
) -> SubscriptionResponse:
    person = SubscriptionUser(username="person", uuid=UUID, short_id=SHORT_ID, status=status)
    return render_subscription(person, endpoints, document, profile_title="Meridian")


def _colliding_endpoints() -> list[ClientEndpoint]:
    remarks = (
        "Repeated",
        "Repeated",
        "Repeated (2)",
        "Automatic",
        "Meridian",
        "direct",
        "DIRECT",
        "REJECT",
        "Automatic · Firefox",
        "Repeated",
    )
    endpoint = FLEETS["single"].endpoints[0]
    return [
        endpoint.model_copy(
            update={
                "remark": remark,
                "position": position,
                "fingerprint_copy": position == len(remarks) - 1,
                "fingerprint": "firefox" if position == len(remarks) - 1 else "chrome",
            }
        )
        for position, remark in enumerate(remarks)
    ]


@pytest.mark.parametrize("document", ["base64", "browser", "mihomo", "stash", "singbox"])
def test_presentation_names_are_unique_and_reserve_generated_groups(document: DocumentFormat) -> None:
    endpoints = _colliding_endpoints()
    original = [endpoint.model_dump() for endpoint in endpoints]
    response = _render_endpoints(endpoints, document)
    expected = [
        "Repeated",
        "Repeated (3)",
        "Repeated (2)",
        "Automatic (2)",
        "Meridian (2)",
        "direct (2)",
        "DIRECT (2)",
        "REJECT (2)",
        "Automatic · Firefox (2)",
        "Repeated (4)",
    ]
    if document in ("base64", "browser"):
        lines = (
            base64.b64decode(response.body).decode().splitlines()
            if document == "base64"
            else json.loads(response.body)["links"]
        )
        names = [unquote(urlsplit(line).fragment) for line in lines]
    elif document == "singbox":
        parsed = parse_singbox_subscription(response.body)
        names = [outbound["tag"] for outbound in parsed["outbounds"][3:]]
        expected = expected[:-1]
        tags = [outbound["tag"] for outbound in parsed["outbounds"]]
        assert len(tags) == len(set(tags))
    else:
        parsed = parse_mihomo_subscription(response.body)
        names = [proxy["name"] for proxy in parsed["proxies"]]
        group_names = [group["name"] for group in parsed["proxy-groups"]]
        assert len(names + group_names) == len(set(names + group_names))
        automatic = next(group for group in parsed["proxy-groups"] if group["name"] == "Automatic")
        if document == "stash":
            assert automatic["proxies"] == expected[:-1]
        else:
            assert [
                name for name in automatic["proxies"] if not re.search(automatic["exclude-filter"], name)
            ] == expected[:-1]
    assert names == expected
    assert [endpoint.model_dump() for endpoint in endpoints] == original


def test_duplicate_names_have_a_stable_order_when_positions_tie() -> None:
    endpoint = FLEETS["single"].endpoints[0]
    endpoints = [
        endpoint.model_copy(update={"remark": "Repeated", "position": 0, "address": f"198.51.100.{index}"})
        for index in (10, 11, 12)
    ]
    assert _render_endpoints(endpoints, "base64").body == _render_endpoints(endpoints[::-1], "base64").body


@pytest.mark.parametrize(
    ("remarks", "expected_names"),
    [
        (("X", "X", "XA"), ["X", "X (2)", "XA"]),
        (("Exit",) * 10, ["Exit", *(f"Exit ({index})" for index in range(2, 11))]),
        (("Exit", "Exit", "Exit (1)"), ["Exit", "Exit (2)", "Exit (1)"]),
    ],
    ids=["prefix", "double-digit-suffix", "existing-suffix"],
)
@pytest.mark.parametrize("fingerprint_copy", [False, True], ids=["primaries", "copies"])
def test_formats_keep_presentation_order_after_disambiguation(
    remarks: tuple[str, ...], expected_names: list[str], fingerprint_copy: bool
) -> None:
    prototype = FLEETS["single"].endpoints[0]
    endpoints = [
        prototype.model_copy(
            update={
                "remark": remark,
                "position": 0,
                "address": f"198.51.100.{index}",
                "fingerprint_copy": fingerprint_copy,
                "fingerprint": "firefox" if fingerprint_copy else "chrome",
            }
        )
        for index, remark in enumerate(remarks, start=10)
    ]
    expected_addresses = [endpoint.address for endpoint in endpoints]
    if fingerprint_copy:
        endpoints.append(prototype.model_copy(update={"remark": "Primary", "position": 1}))

    links = json.loads(_render_endpoints(endpoints, "browser").body)["links"]
    names = [unquote(urlsplit(link).fragment) for link in links]
    assert names == [*expected_names, *(["Primary"] if fingerprint_copy else [])]
    addresses = [urlsplit(link).hostname for link in links]
    for dialect in ("mihomo", "stash"):
        parsed = yaml.safe_load(_render_endpoints(endpoints, dialect).body)
        assert [proxy["server"] for proxy in parsed["proxies"]] == addresses
        selector = next(group for group in parsed["proxy-groups"] if group["name"] == "Meridian")
        assert selector["proxies"][1:] == names

    singbox = json.loads(_render_endpoints(endpoints, "singbox").body)
    primary_addresses = [endpoints[-1].address] if fingerprint_copy else expected_addresses
    assert [outbound["server"] for outbound in singbox["outbounds"][3:]] == primary_addresses
    configs = json.loads(_render_endpoints(endpoints, "xray-json").body)
    remark = "Automatic · Firefox" if fingerprint_copy else "Automatic"
    selected = next(config for config in configs if config["remarks"] == remark)
    assert [outbound["settings"]["vnext"][0]["address"] for outbound in proxy_outbounds(selected)] == expected_addresses


def test_disambiguated_names_keep_the_endpoint_remark_length_bound() -> None:
    endpoint = FLEETS["single"].endpoints[0]
    endpoints = [endpoint.model_copy(update={"remark": "я" * 100, "position": index}) for index in range(3)]
    lines = base64.b64decode(_render_endpoints(endpoints, "base64").body).decode().splitlines()
    names = [unquote(urlsplit(line).fragment) for line in lines]
    assert len(set(names)) == 3
    assert all(len(name) <= 100 for name in names)
    assert names[1].endswith(" (2)") and names[2].endswith(" (3)")


EMPTY_CASES: list[tuple[DocumentFormat, str]] = [
    (document, "empty") for document in ("base64", "browser", "xray-json", "mihomo", "stash", "singbox")
] + [
    ("stash", "xhttp-only"),
    ("singbox", "xhttp-only"),
    ("xray-json", "copies-only"),
    ("mihomo", "copies-only"),
    ("stash", "copies-only"),
    ("singbox", "copies-only"),
]


def _unavailable_endpoints(case: str) -> list[ClientEndpoint]:
    if case == "empty":
        return []
    if case == "xhttp-only":
        return [endpoint for endpoint in DOMAIN_FLEET.endpoints if endpoint.protocol == "xhttp"]
    return [
        endpoint for endpoint in DOMAIN_FLEET.endpoints if endpoint.fingerprint_copy and endpoint.protocol == "reality"
    ]


@pytest.mark.parametrize(
    ("document", "case"), EMPTY_CASES, ids=[f"{document}-{case}" for document, case in EMPTY_CASES]
)
def test_empty_or_filtered_subscriptions_have_inert_placeholders(document: DocumentFormat, case: str) -> None:
    response = _render_endpoints(_unavailable_endpoints(case), document)
    if document in ("base64", "browser"):
        lines = parse_base64_subscription(response.body) if document == "base64" else json.loads(response.body)["links"]
        names = [unquote(urlsplit(line).fragment) for line in lines]
        assert all(
            urlsplit(line).hostname == "0.0.0.0"
            and urlsplit(line).port == 1
            and urlsplit(line).username == "00000000-0000-0000-0000-000000000000"
            for line in lines
        )
    elif document == "xray-json":
        configs = json.loads(response.body)
        names = [config["remarks"] for config in configs]
        assert all(not proxy_outbounds(config) for config in configs)
    elif document == "singbox":
        parsed = parse_singbox_subscription(response.body)
        proxies = parsed["outbounds"][3:]
        names = [proxy["tag"] for proxy in proxies]
        assert all(
            proxy["server"] == "0.0.0.0"
            and proxy["server_port"] == 1
            and proxy["uuid"] == "00000000-0000-0000-0000-000000000000"
            for proxy in proxies
        )
    else:
        parsed = parse_mihomo_subscription(response.body)
        proxies = parsed["proxies"]
        names = [proxy["name"] for proxy in proxies]
        assert all(
            proxy["server"] == "0.0.0.0"
            and proxy["port"] == 1
            and proxy["uuid"] == "00000000-0000-0000-0000-000000000000"
            for proxy in proxies
        )
    assert names[0] == "No connections available yet"
    assert len(names) == 4 and len(set(names)) == 4


@pytest.mark.parametrize("status", ["disabled", "limited", "expired"])
def test_status_notices_take_priority_over_no_connections(status: UserStatus) -> None:
    lines = parse_base64_subscription(_render_endpoints([], "base64", status).body)
    assert [unquote(urlsplit(line).fragment) for line in lines] == list(STATUS_REMARKS[status])


@pytest.mark.parametrize("status", ["active", "disabled"])
def test_mihomo_copy_exclusions_never_remove_placeholders(status: UserStatus) -> None:
    remarks = NO_CONNECTIONS_REMARKS if status == "active" else STATUS_REMARKS[status]
    prototype = DOMAIN_FLEET.endpoints[0]
    endpoints = [
        prototype.model_copy(update={"remark": remark, "position": position, "fingerprint_copy": True})
        for position, remark in enumerate(remarks)
    ]
    parsed = parse_mihomo_subscription(_render_endpoints(endpoints, "mihomo", status).body)
    automatic = next(group for group in parsed["proxy-groups"] if group["name"] == "Automatic")
    pattern = automatic.get("exclude-filter")
    assert [name for name in automatic["proxies"] if not pattern or not re.search(pattern, name)] == list(remarks)


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


@pytest.mark.parametrize("topology", ["single", "fleet", "domain"])
@pytest.mark.parametrize("routing", ["ru-split", "full"])
def test_xray_offers_one_named_profile_per_primary_with_the_shared_routing(topology: str, routing: str) -> None:
    endpoints = ALL_FLEETS[topology].endpoints
    configs = xray_json_document(endpoints, UUID, client_routing=routing)
    primary_endpoints = primaries(presentation_endpoints(endpoints))
    assert configs[0]["remarks"] == "Automatic"
    assert [config["remarks"] for config in configs[1 : 1 + len(primary_endpoints)]] == [
        endpoint.remark for endpoint in primary_endpoints
    ]
    automatic = proxy_outbounds(configs[0])
    for profile, outbound in zip(configs[1:], automatic, strict=False):
        assert proxy_outbounds(profile) == [{**outbound, "tag": "MERIDIAN_PROXY"}]
        assert profile["routing"] == xray_json_template(client_routing=routing)["routing"]
        assert profile["burstObservatory"] == configs[0]["burstObservatory"]


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


# The corpus holds no WebSocket entry, so these rows follow Remnawave 3.4.5's generators
# (`src/modules/subscription-template/generators/`) for a Host Meridian compiles for WSS:
# its fields in their order, the Host as Mihomo's and sing-box's `Host` header, and Xray's
# empty `headers`. Stash reads Mihomo's WebSocket options under its own `sni`.
_WS_PROXY = {
    "name": WSS.remark,
    "type": "vless",
    "server": DOMAIN,
    "port": 443,
    "network": "ws",
    "udp": True,
    "uuid": UUID,
    "packet-encoding": "xudp",
    "tls": True,
    "servername": DOMAIN,
    "ws-opts": {"path": f"/{WS_PATH}", "headers": {"Host": DOMAIN}},
    "client-fingerprint": "chrome",
}
WSS_ENTRIES: dict[DocumentFormat, Any] = {
    "base64": f"vless://{UUID}@{DOMAIN}:443?encryption=none&type=ws&path=%2F{WS_PATH}&host={DOMAIN}"
    f"&security=tls&sni={DOMAIN}&fp=chrome#WebSocket%20%C2%B7%20CDN",
    "xray-json": {
        "protocol": "vless",
        "settings": {
            "vnext": [{"address": DOMAIN, "port": 443, "users": [{"id": UUID, "encryption": "none", "flow": ""}]}]
        },
        "streamSettings": {
            "network": "ws",
            "wsSettings": {"path": f"/{WS_PATH}", "host": DOMAIN, "headers": {}},
            "security": "tls",
            "tlsSettings": {"serverName": DOMAIN, "enableSessionResumption": False, "fingerprint": "chrome"},
        },
    },
    "mihomo": _WS_PROXY,
    "stash": {("sni" if key == "servername" else key): value for key, value in _WS_PROXY.items()},
    "singbox": {
        "type": "vless",
        "tag": WSS.remark,
        "server": DOMAIN,
        "server_port": 443,
        "uuid": UUID,
        "transport": {"type": "ws", "path": f"/{WS_PATH}", "headers": {"Host": DOMAIN}},
        "tls": {"enabled": True, "server_name": DOMAIN, "utls": {"enabled": True, "fingerprint": "chrome"}},
    },
}


def _wss_entry(document: DocumentFormat, body: str) -> Any:
    if document == "base64":
        return next(line for line in base64.b64decode(body).decode().splitlines() if "type=ws&" in line)
    if document == "xray-json":
        outbounds = json.loads(body)[0]["outbounds"]
        found = next(item for item in outbounds if item.get("streamSettings", {}).get("network") == "ws")
        return {key: value for key, value in found.items() if key != "tag"}
    if document == "singbox":
        return next(item for item in json.loads(body)["outbounds"] if item["tag"] == WSS.remark)
    return next(proxy for proxy in yaml.safe_load(body)["proxies"] if proxy["name"] == WSS.remark)


@pytest.mark.parametrize(("strategy", "shown"), [("no_reset", "NO_RESET"), ("month", "MONTH"), ("day", "DAY")])
def test_the_browser_answer_names_the_users_reset_strategy(strategy: str, shown: str) -> None:
    info = json.loads(_render("single", "browser", limit_strategy=strategy).body)["user"]
    assert info["trafficLimitStrategy"] == shown


@pytest.mark.parametrize("document", sorted(WSS_ENTRIES))
def test_wss_entries_match_remnawave_generators(document: DocumentFormat) -> None:
    entry = _wss_entry(document, _render("domain", document).body)
    expected = WSS_ENTRIES[document]
    assert entry == expected
    if isinstance(expected, dict):
        assert list(entry) == list(expected)


def test_wss_joins_every_failover_group() -> None:
    """WSS is a primary entry: unlike XHTTP, sing-box and Stash speak it too."""
    mihomo = yaml.safe_load(_render("domain", "mihomo").body)
    stash = yaml.safe_load(_render("domain", "stash").body)
    for document in (mihomo, stash):
        assert (
            WSS.remark in next(group for group in document["proxy-groups"] if group["name"] == "Automatic")["proxies"]
        )
    singbox = json.loads(_render("domain", "singbox").body)
    assert WSS.remark in next(item for item in singbox["outbounds"] if item["type"] == "urltest")["outbounds"]
    automatic = json.loads(_render("domain", "xray-json").body)[0]
    balancer = automatic["routing"]["balancers"][0]["selector"]
    ws_tag = next(item["tag"] for item in proxy_outbounds(automatic) if item["streamSettings"]["network"] == "ws")
    assert any(ws_tag.startswith(prefix) for prefix in balancer)


def test_a_safari_paths_two_copies_get_an_automatic_config_each() -> None:
    endpoints = [
        ClientEndpoint(
            remark=f"Reality · {label}",
            protocol="reality",
            address=EXIT_B,
            port=443,
            position=position,
            fingerprint=label.lower(),
            reality_sni=DOMAIN,
            reality_public_key=KEYS["exit-b"][0],
            reality_short_id=KEYS["exit-b"][1],
            fingerprint_copy=label != "Safari",
        )
        for position, label in enumerate(("Safari", "Chrome", "Firefox"))
    ]

    configs = xray_json_document(endpoints, UUID)

    fingerprints = {
        config["remarks"]: sorted(
            outbound["streamSettings"]["realitySettings"]["fingerprint"]
            for outbound in config["outbounds"]
            if outbound.get("protocol") == "vless"
        )
        for config in configs
    }
    assert fingerprints == {
        "Automatic": ["safari"],
        "Reality · Safari": ["safari"],
        "Automatic · Chrome": ["chrome"],
        "Automatic · Firefox": ["firefox"],
    }


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


@pytest.mark.parametrize("document", ["mihomo", "stash", "singbox"])
@pytest.mark.parametrize("case", ["empty", "xhttp-only", "copies-only", "duplicate-names"])
def test_client_engines_accept_availability_and_name_collision_cases(
    document: DocumentFormat, case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    singbox = document == "singbox"
    binary = _engine("MERIDIAN_TEST_SINGBOX" if singbox else "MERIDIAN_TEST_MIHOMO")
    monkeypatch.setenv("PATH", f"{binary.parent}{os.pathsep}{os.environ['PATH']}")
    geodata = None if singbox else os.environ.get("MERIDIAN_TEST_MIHOMO_GEODATA")
    endpoints = _colliding_endpoints() if case == "duplicate-names" else _unavailable_endpoints(case)
    check_with_engine(
        SINGBOX_CLIENT_TYPE if singbox else MIHOMO_CLIENT_TYPE,
        _render_endpoints(endpoints, document).body,
        tmp_path,
        geodata_dir=Path(geodata) if geodata else None,
    )


@pytest.mark.parametrize("short_id", ["12e34567", "01890000", "1234"], ids=["float", "leading-zero-int", "int"])
@pytest.mark.parametrize("stash", [False, True], ids=["mihomo", "stash"])
def test_a_numeric_looking_short_id_stays_a_quoted_string_for_yaml_12_readers(short_id: str, stash: bool) -> None:
    from meridian.control.formats.mihomo import mihomo_document

    endpoint = ClientEndpoint(
        remark="Reality",
        protocol="reality",
        address=EXIT_B,
        port=443,
        position=0,
        reality_sni=DOMAIN,
        reality_public_key=KEYS["exit-b"][0],
        reality_short_id=short_id,
    )

    text = mihomo_document([endpoint], UUID, stash=stash)

    assert f"short-id: '{short_id}'" in text
    assert yaml.safe_load(text)["proxies"][0]["reality-opts"]["short-id"] == short_id
