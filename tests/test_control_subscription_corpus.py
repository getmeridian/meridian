"""The control service answers every client as the golden corpus recorded, except where Meridian fixes it.

Each corpus request (`tests/systemlab/golden/`) is rendered from service inputs equivalent to
the System Lab fleet it was captured from, normalized like the corpus, and compared: status,
format, headers, and body (Base64 link lists byte for byte, JSON and YAML as documents, since
key order and YAML layout mean nothing to a client). Every deliberate difference is a named
row below: either a header rule (what nginx adds, what the panel sent that Meridian drops) or
a body rule that says which recorded answer a client gets instead, and why.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
import yaml

from meridian.control.formats.entries import SubscriptionUser
from meridian.control.formats.selector import SUFFIX_FORMATS, select_format
from meridian.control.models import ClientEndpoint
from meridian.control.subscriptions import SubscriptionResponse, render_subscription, unknown_subscription
from tests.systemlab.golden_corpus import Capture, Response, decode_body, load_corpus, normalize_response

CORPUS = load_corpus()
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)

# -- service inputs equivalent to each captured fleet ---------------------------------------

EXIT_A, EXIT_B, RELAY_A, GATEWAY_A = "198.51.100.11", "198.51.100.12", "198.51.100.21", "198.51.100.31"
UUID = "3f2a9c4e-7b1d-4e6a-9c2f-1a2b3c4d5e6f"
SHORT_ID = "Zq8xT3vNw2LmK7pR"
SUB_PATH = "connect7f3a9b2c"
XHTTP_PATH = "x9k2m4n6p8q1"
SALAMANDER = "salamander-secret-value"
KEYS = {  # per exit workload: (Reality public key, short ID); real X25519 keys, so engines accept them
    "exit-a": ("pOCSkrZRwni5dyxWn1-puxPZBrRqtoyd-dwrRAn4ogk", "a1a1a1a1"),
    "exit-b": ("zo060cy2M-x7cMF4FKXHbs0CloUFDTRHRboFhw5YfVk", "b2b2b2b2"),
    "gateway-a": ("Xf7dO2vUf2-ijuFdlp1bsOpTd01Ii9r53xxuASSz7yI", "c3c3c3c3"),
}


def _reality(remark: str, address: str, keys: str, position: int, *, sni: str = "www.google.com") -> ClientEndpoint:
    fingerprint = "firefox" if "Firefox" in remark else "chrome"
    public_key, short_id = KEYS[keys]
    return ClientEndpoint(
        remark=remark,
        protocol="reality",
        address=address,
        port=443,
        position=position,
        fingerprint=fingerprint,
        reality_sni=sni,
        reality_public_key=public_key,
        reality_short_id=short_id,
        fingerprint_copy=fingerprint != "chrome",
    )


def _xhttp(remark: str, address: str, position: int) -> ClientEndpoint:
    fingerprint = "firefox" if "Firefox" in remark else "chrome"
    return ClientEndpoint(
        remark=remark,
        protocol="xhttp",
        address=address,
        port=443,
        position=position,
        fingerprint=fingerprint,
        tls_sni=address,
        host=address,
        path=XHTTP_PATH,
        fingerprint_copy=fingerprint != "chrome",
    )


def _hysteria2(remark: str, address: str, position: int) -> ClientEndpoint:
    return ClientEndpoint(
        remark=remark,
        protocol="hysteria2",
        address=address,
        port=443,
        position=position,
        tls_sni=address,
        salamander_password=SALAMANDER,
    )


@dataclass(frozen=True)
class Fleet:
    username: str
    title: str
    control: str
    endpoints: list[ClientEndpoint]
    hosts: dict[str, str] = field(default_factory=dict)

    @property
    def public_url(self) -> str:
        return f"https://{self.control}/{SUB_PATH}"

    def known(self) -> list[tuple[str, str]]:
        """The values the capture script told the normalizer about, in its kinds."""
        known = [("short-uuid", SHORT_ID), ("vless-uuid", UUID), ("sub-path", SUB_PATH), ("path", XHTTP_PATH)]
        known += [("salamander", SALAMANDER)]
        for public_key, short_id in KEYS.values():
            known += [("reality-pbk", public_key), ("short-id", short_id)]
        return known + [(f"host:{role}", address) for role, address in self.hosts.items()]


FLEETS = {
    "single": Fleet(
        "solo",
        "Meridian",
        EXIT_B,
        [
            _reality("Reality · Chrome", EXIT_B, "exit-b", 0),
            _reality("Reality · Firefox", EXIT_B, "exit-b", 1),
            _xhttp("XHTTP · Chrome", EXIT_B, 2),
            _xhttp("XHTTP · Firefox", EXIT_B, 3),
            _hysteria2("Hysteria2 · UDP", EXIT_B, 4),
        ],
        {"exit-b": EXIT_B},
    ),
    "fleet": Fleet(
        "acceptance",
        "Meridian system lab",
        EXIT_A,
        [
            _reality("exit-a · Reality · Chrome", EXIT_A, "exit-a", 0),
            _reality("exit-a · Reality · Firefox", EXIT_A, "exit-a", 1),
            _reality("exit-a · Reality · Chrome via relay-chain", RELAY_A, "exit-a", 2, sni="www.cloudflare.com"),
            _reality("exit-a · Reality · Firefox via relay-chain", RELAY_A, "exit-a", 3, sni="www.cloudflare.com"),
            _reality("exit-b · Reality · Chrome", EXIT_B, "exit-b", 4),
            _reality("exit-b · Reality · Firefox", EXIT_B, "exit-b", 5),
            _reality("Smart routing", GATEWAY_A, "gateway-a", 6),
        ],
        {"exit-a": EXIT_A, "exit-b": EXIT_B, "relay-a": RELAY_A, "gateway-a": GATEWAY_A},
    ),
    "reduced": Fleet(
        "acceptance",
        "Meridian system lab",
        EXIT_A,
        [
            _reality("Reality · Chrome", EXIT_A, "exit-a", 0),
            _reality("Reality · Firefox", EXIT_A, "exit-a", 1),
            _reality("Smart routing", GATEWAY_A, "gateway-a", 2),
        ],
        {"exit-a": EXIT_A, "gateway-a": GATEWAY_A},
    ),
}

# -- deliberate deviations from the corpus ---------------------------------------------------

# Added by the control server's nginx in front of the service (`location /<path>/sub/`):
# Server/Date replace the upstream's, Vary and Content-Encoding come with gzip (JSON and
# Base64 only: `gzip_types` has no YAML, so YAML stays uncompressed, as it was), and its own
# Referrer-Policy is the only one left (the panel sent a duplicate).
NGINX_HEADERS = frozenset({"server", "date", "vary", "content-encoding", "referrer-policy"})
# The panel's browser-security headers (helmet): a CSP naming `ungh.cc` and GitHub, HSTS,
# frame and cross-origin policies. A subscription is not a web page; Meridian drops them.
PANEL_SECURITY_HEADERS = frozenset(
    {
        "content-security-policy",
        "cross-origin-opener-policy",
        "cross-origin-resource-policy",
        "origin-agent-cluster",
        "strict-transport-security",
        "x-dns-prefetch-control",
        "x-download-options",
        "x-frame-options",
        "x-permitted-cross-domain-policies",
        "x-xss-protection",
    }
)
# Express's ETag (a volatile header) is not sent; every answer says Cache-Control: no-store
# instead, so no cache or CDN in front ever keeps a document that carries credentials.
DROPPED_HEADERS = NGINX_HEADERS | PANEL_SECURITY_HEADERS | {"etag", "content-length"}
ADDED_HEADERS = (("cache-control", "no-store"),)


def _stash_dialect(text: str) -> str:
    """Meridian's Stash document from the recorded Mihomo one: no XHTTP, no exclude-filter
    (the fallback group lists primary entries only), Stash's `GEOIP,LAN`, and its field
    names (`sni`, Hysteria2 `auth`)."""
    document = yaml.safe_load(text)
    xhttp = {proxy["name"] for proxy in document["proxies"] if proxy.get("network") == "xhttp"}
    renamed = {"servername": "sni", "password": "auth"}
    document["proxies"] = [
        {renamed.get(key, key): value for key, value in proxy.items()}
        for proxy in document["proxies"]
        if proxy["name"] not in xhttp
    ]
    for group in document["proxy-groups"]:
        excluded = set(xhttp)
        if pattern := group.pop("exclude-filter", None):
            excluded |= set(pattern.removeprefix("^(").removesuffix(")$").split("|"))
            group["proxies"] = [name for name in group["proxies"] if name not in excluded]
        else:
            group["proxies"] = [name for name in group["proxies"] if name not in xhttp]
    document["rules"] = [rule.replace("GEOIP,private,", "GEOIP,LAN,") for rule in document["rules"]]
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


@dataclass(frozen=True)
class Deviation:
    """A corpus row Meridian answers differently: with what *source* row got, maybe transformed."""

    reason: str
    source: str
    transform: Callable[[str], str] | None = None


_AS_MIHOMO = "Remnawave sent its default Clash template with no proxies; Meridian sends its real Mihomo document."
_AS_STASH = (
    "Remnawave sent its default Stash template (no failover group); "
    "Meridian sends its Mihomo document in Stash's dialect."
)
_AS_SINGBOX = (
    "Hiddify (sing-box inside) got Base64 without failover; Meridian sends sing-box JSON with its urltest group."
)
_AS_BASE64 = "Remnawave refused a missing or empty User-Agent with 403; Meridian serves Base64 like any unknown app."

DEVIATIONS: dict[str, Deviation] = {
    "clash-for-android": Deviation(_AS_MIHOMO, "mihomo"),
    "disabled--clash-for-android": Deviation(_AS_MIHOMO, "disabled--flclashx"),
    "stash": Deviation(_AS_STASH, "mihomo", _stash_dialect),
    "disabled--stash": Deviation(_AS_STASH, "disabled--flclashx", _stash_dialect),
    "hiddify": Deviation(_AS_SINGBOX, "sing-box-android"),
    "empty": Deviation(_AS_BASE64, "curl"),
    "absent": Deviation(_AS_BASE64, "curl"),
}

# Apps that stay on Base64 although they lack failover there: no subscription format they
# document carries a failover group (see `formats.selector`). Named so the choice is explicit.
KEPT_BASE64 = ("nekobox", "throne", "v2raytun", "shadowrocket")

# -- rendering and comparison ----------------------------------------------------------------


def _render(capture: Capture) -> SubscriptionResponse:
    """What the service answers this request, from the fleet's equivalent inputs."""
    request = capture.request
    if request["user"] == "unknown":
        return unknown_subscription()
    fleet = FLEETS[capture.topology]
    status = "disabled" if request["user"] == "disabled" else "active"
    user = SubscriptionUser(username=fleet.username, uuid=UUID, short_id=SHORT_ID, status=status)
    document = SUFFIX_FORMATS[request["selector"]] if request["selector"] else None
    document = document or select_format(request["user_agent"], request["accept"])
    return render_subscription(
        user, fleet.endpoints, document, profile_title=fleet.title, public_url=fleet.public_url, now=NOW
    )


def _normalized(capture: Capture, response: SubscriptionResponse) -> Response:
    headers = [("content-type", response.content_type), *ADDED_HEADERS, *response.headers.items()]
    encoding, text = decode_body(response.content_type, response.body.encode("utf-8"))
    return normalize_response(response.status, headers, text, encoding=encoding, known=FLEETS[capture.topology].known())


def _expected(capture: Capture) -> Response:
    golden = capture.response
    deviation = DEVIATIONS.get(str(capture.request["id"]))
    if deviation is not None:
        golden = CORPUS[(capture.topology, deviation.source)].response
        if deviation.transform is not None:
            golden = Response(
                golden.status, golden.format, golden.encoding, golden.headers, deviation.transform(golden.body)
            )
    headers = tuple(pair for pair in golden.headers if pair[0] not in DROPPED_HEADERS) + ADDED_HEADERS
    return Response(golden.status, golden.format, golden.encoding, headers, golden.body)


def _document(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return yaml.safe_load(text)


def _differences(expected: Response, served: Response) -> list[str]:
    problems = []
    if (served.status, served.format, served.encoding) != (expected.status, expected.format, expected.encoding):
        served_kind = f"{served.status} {served.format}/{served.encoding}"
        problems.append(f"{served_kind} != {expected.status} {expected.format}/{expected.encoding}")
    wanted = sorted(pair for pair in expected.headers if pair[0] != "content-length")
    got = sorted(pair for pair in served.headers if pair[0] != "content-length")
    problems += [f"header missing: {pair}" for pair in wanted if pair not in got]
    problems += [f"header unexpected: {pair}" for pair in got if pair not in wanted]
    if expected.encoding == "base64":
        if served.body != expected.body:
            problems.append(f"links differ:\n{served.body}\n!=\n{expected.body}")
    elif _document(served.body) != _document(expected.body):
        problems.append(f"document differs:\n{served.body}\n!=\n{expected.body}")
    return problems


KEYS_ALL = sorted(CORPUS)


@pytest.mark.parametrize("key", KEYS_ALL, ids=[f"{topology}/{request}" for topology, request in KEYS_ALL])
def test_service_answers_like_the_corpus(key: tuple[str, str]) -> None:
    capture = CORPUS[key]
    deviation = DEVIATIONS.get(key[1])
    reason = f"deliberate deviation: {deviation.reason}" if deviation else "matches the corpus"
    assert _differences(_expected(capture), _normalized(capture, _render(capture))) == [], reason


@pytest.mark.parametrize("request_id", sorted(DEVIATIONS))
def test_every_deviation_names_a_recorded_request_and_source(request_id: str) -> None:
    for topology in FLEETS:
        if (topology, request_id) in CORPUS:
            assert (topology, DEVIATIONS[request_id].source) in CORPUS
    assert any((topology, request_id) in CORPUS for topology in FLEETS)


@pytest.mark.parametrize("request_id", KEPT_BASE64)
def test_apps_without_a_failover_format_keep_base64(request_id: str) -> None:
    capture = CORPUS[("fleet", request_id)]
    assert capture.response.format == "base64" and request_id not in DEVIATIONS
    assert _normalized(capture, _render(capture)).format == "base64"


def test_the_fleet_corpus_covers_every_format_and_status() -> None:
    formats = {_normalized(c, _render(c)).format for k, c in CORPUS.items() if k[0] == "fleet"}
    assert formats == {"base64", "xray-json", "clash-yaml", "singbox", "json"}
