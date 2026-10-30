"""The golden subscription corpus: fixtures are well-formed, secret-free, and stable."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from tests.systemlab.golden_agents import AGENTS, SELECTORS, STATUS_AGENT_IDS, UNKNOWN_AGENT_IDS
from tests.systemlab.golden_corpus import (
    GOLDEN_DIR,
    HOP_BY_HOP_HEADERS,
    TOPOLOGIES,
    VOLATILE,
    VOLATILE_HEADERS,
    Capture,
    compare_corpus,
    compare_response,
    decode_body,
    detect_format,
    load_corpus,
    main,
    normalize_raw,
    normalize_response,
    served_body,
    stored_body,
)

CORPUS = load_corpus()
KEYS = sorted(CORPUS)
_RAW_SECRET = re.compile(
    r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}"  # any UUID
    r"|172\.30\.0\.\d"  # a lab address
    r"|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}"  # a timestamp
)


def _ids(keys: list[tuple[str, str]]) -> list[str]:
    return [f"{topology}/{request_id}" for topology, request_id in keys]


def test_every_topology_records_every_agent_and_selector() -> None:
    assert sorted({topology for topology, _ in CORPUS}) == sorted(TOPOLOGIES)
    for topology in TOPOLOGIES:
        recorded = {request_id for t, request_id in CORPUS if t == topology}
        expected = {agent.id for agent in AGENTS} | {f"selector-{selector}" for selector in SELECTORS}
        assert expected <= recorded, (topology, sorted(expected - recorded))
    fleet = {request_id for topology, request_id in CORPUS if topology == "fleet"}
    assert {f"disabled--{agent_id}" for agent_id in STATUS_AGENT_IDS} <= fleet
    assert {f"unknown--{agent_id}" for agent_id in UNKNOWN_AGENT_IDS} <= fleet


def test_manifest_records_the_rules_and_agents() -> None:
    manifest = json.loads((GOLDEN_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["remnawave_backend"].startswith("remnawave/backend:")
    assert [rule["name"] for rule in manifest["response_rules"]["rules"]]
    assert [agent["id"] for agent in manifest["agents"]] == [agent.id for agent in AGENTS]


@pytest.mark.parametrize("key", KEYS, ids=_ids(KEYS))
def test_fixture_is_well_formed(key: tuple[str, str]) -> None:
    topology, request_id = key
    path = GOLDEN_DIR / topology / f"{request_id}.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    response = fixture["response"]
    assert isinstance(response["status"], int)
    for name, value in response["headers"]:
        assert name == name.lower() and name not in HOP_BY_HOP_HEADERS
        assert name not in VOLATILE_HEADERS or value == VOLATILE
    body = response["body"]
    if body is not None:
        stored = (path.parent / body["file"]).read_text(encoding="utf-8")
        assert stored_body(served_body(stored, body["layout"])) == (body["layout"], stored)
        # The digest covers the (normalized) bytes a client received, not the reflowed file.
        assert hashlib.sha256(CORPUS[key].response.body.encode("utf-8")).hexdigest() == body["sha256"]
        assert body["encoding"] in {"identity", "base64"}
    capture = CORPUS[key]
    assert (
        detect_format(
            capture.response.header("content-type"), capture.response.body, encoding=capture.response.encoding
        )
        == response["format"]
    )


@pytest.mark.parametrize("key", KEYS, ids=_ids(KEYS))
def test_fixture_holds_no_raw_secret(key: tuple[str, str]) -> None:
    capture = CORPUS[key]
    for text in (capture.response.body, json.dumps(capture.response.headers)):
        assert not _RAW_SECRET.search(text), _RAW_SECRET.search(text)


@pytest.mark.parametrize("key", KEYS, ids=_ids(KEYS))
def test_normalizer_is_idempotent_on_fixture(key: tuple[str, str]) -> None:
    response = CORPUS[key].response
    again = normalize_response(response.status, response.headers, response.body, encoding=response.encoding)
    assert again == response


@pytest.mark.parametrize("key", KEYS, ids=_ids(KEYS))
def test_matched_rule_is_the_expected_rule(key: tuple[str, str]) -> None:
    capture = CORPUS[key]
    if capture.matched is None or capture.expected is None:
        pytest.skip("no recorded match (not-found, selector, or browser answer)")
    assert capture.matched == capture.expected


@pytest.mark.parametrize("key", KEYS, ids=_ids(KEYS))
def test_unrecorded_answers_have_their_expected_status(key: tuple[str, str]) -> None:
    """Remnawave records no rule for these, so the status is the evidence."""
    capture = CORPUS[key]
    if capture.request["user"] == "unknown":
        assert (capture.response.status, capture.response.format) == (404, "json")
    elif capture.expected is not None and capture.expected["response_type"] == "FORBIDDEN":
        assert (capture.response.status, capture.response.format) == (403, "json")
    elif capture.expected is not None and capture.expected["response_type"] == "BROWSER":
        assert (capture.response.status, capture.response.format) == (200, "json")
        assert json.loads(capture.response.body)["isFound"] is True
    else:
        assert capture.response.status == 200


def test_corpus_matches_itself() -> None:
    assert compare_corpus(CORPUS, CORPUS) == {}


def _raw(body: str, headers: list[list[str]], *, encode: bool = False) -> dict[str, object]:
    payload = base64.b64encode(body.encode()).decode() if encode else body
    return {
        "topology": "fleet",
        "request": {
            "id": "happ",
            "agent": "happ",
            "user": "active",
            "selector": "",
            "user_agent": "Happ/1",
            "accept": "*/*",
        },
        "matched": None,
        "response": {"status": 200, "headers": headers, "body_b64": base64.b64encode(payload.encode()).decode()},
    }


_KNOWN = [
    ("vless-uuid", "0f8e2c1a-1b2c-4d3e-8f4a-5b6c7d8e9f00"),
    ("short-uuid", "Zq7Lm2Np4Rs6"),
    ("short-id", "ab12cd34"),
    ("path", "a1b2c3d4e5"),
    ("host:exit-a", "198.51.100.10"),
]
_LINKS = (
    "vless://0f8e2c1a-1b2c-4d3e-8f4a-5b6c7d8e9f00@198.51.100.10:443?security=reality&pbk=Q1w2E3r4_T5y6-U7i8"
    "&sid=ab12cd34#Reality\n"
    "vless://0f8e2c1a-1b2c-4d3e-8f4a-5b6c7d8e9f00@198.51.100.10:443?type=xhttp&path=%2Fa1b2c3d4e5#XHTTP\n"
    "hysteria2://0f8e2c1a-1b2c-4d3e-8f4a-5b6c7d8e9f00@198.51.100.10:443?obfs=salamander&obfs-password=s3cr3t#H2\n"
)


def test_base64_body_is_decoded_and_numbered_by_first_appearance() -> None:
    capture = normalize_raw(
        _raw(
            _LINKS,
            [
                ["Content-Type", "text/plain; charset=utf-8"],
                ["Connection", "keep-alive"],
                ["Subscription-Userinfo", "upload=12; download=34; total=0; expire=0"],
                ["Support-Url", "https://198.51.100.10/a1b2c3d4e5/support.html#Zq7Lm2Np4Rs6"],
            ],
            encode=True,
        ),
        _KNOWN,
    )
    response = capture.response
    assert (response.format, response.encoding) == ("base64", "base64")
    assert response.body.splitlines() == [
        "vless://<vless-uuid:1>@<host:exit-a>:443?security=reality&pbk=<reality-pbk:1>&sid=<short-id:1>#Reality",
        "vless://<vless-uuid:1>@<host:exit-a>:443?type=xhttp&path=%2F<path:1>#XHTTP",
        "hysteria2://<vless-uuid:1>@<host:exit-a>:443?obfs=salamander&obfs-password=<password:1>#H2",
    ]
    assert response.headers == (
        ("content-type", "text/plain; charset=utf-8"),
        ("subscription-userinfo", "upload=<bytes>; download=<bytes>; total=0; expire=0"),
        ("support-url", "https://<host:exit-a>/<path:1>/support.html#<short-uuid:1>"),
    )


def test_unknown_secrets_in_json_and_yaml_fields_are_replaced() -> None:
    xray = json.dumps(
        [
            {
                "outbounds": [
                    {
                        "settings": {"id": "c0ffee00-0000-4000-8000-000000000001"},
                        "realitySettings": {"publicKey": "KeyOne", "shortId": "0011"},
                    },
                    {"realitySettings": {"publicKey": "KeyTwo", "shortId": "0011"}},
                ]
            }
        ]
    )
    response = normalize_response(200, [("content-type", "application/json")], xray)
    assert json.loads(response.body) == [
        {
            "outbounds": [
                {
                    "settings": {"id": "<uuid:1>"},
                    "realitySettings": {"publicKey": "<reality-pbk:1>", "shortId": "<short-id:1>"},
                },
                {"realitySettings": {"publicKey": "<reality-pbk:2>", "shortId": "<short-id:1>"}},
            ]
        }
    ]
    mihomo = (
        "proxies:\n  - name: a\n    server: 172.30.0.11\n"
        "    reality-opts:\n      public-key: KeyOne\n      short-id: '0011'\n"
    )
    normalized = normalize_response(200, [("content-type", "text/yaml")], mihomo)
    assert "server: <lab-ip:1>" in normalized.body
    assert "public-key: <reality-pbk:1>" in normalized.body and "short-id: '<short-id:1>'" in normalized.body
    assert normalized.format == "clash-yaml"


def test_normalizer_is_deterministic_and_idempotent() -> None:
    first = normalize_raw(_raw(_LINKS, [["content-type", "text/plain"]], encode=True), _KNOWN).response
    assert normalize_raw(_raw(_LINKS, [["content-type", "text/plain"]], encode=True), _KNOWN).response == first
    assert normalize_response(first.status, first.headers, first.body, encoding=first.encoding) == first


def test_decode_body_leaves_non_link_text_alone() -> None:
    assert decode_body("text/plain", b"Not Found") == ("identity", "Not Found")
    assert decode_body("application/json", base64.b64encode(b"vless://x")) == (
        "identity",
        base64.b64encode(b"vless://x").decode(),
    )


def test_comparator_ignores_volatile_headers_and_reports_real_drift() -> None:
    golden = normalize_response(200, [("content-type", "application/json"), ("date", "a")], '{"a": [1, 2]}')
    same = replace(golden, headers=(("content-type", "application/json"), ("date", "b")))
    assert compare_response(golden, same) == []
    reformatted = replace(golden, body='{"a":[1,2]}')
    assert compare_response(golden, reformatted) == ["body: same document, different bytes (formatting or key order)"]
    drifted = replace(golden, status=404, headers=(("content-type", "text/plain"),), body="{}")
    problems = compare_response(golden, drifted)
    assert problems[0] == "status 404 != 200"
    assert any(problem.startswith("header missing or changed: content-type") for problem in problems)
    assert any(problem.startswith("body differs") for problem in problems)


def test_compare_cli_reports_missing_and_extra_captures(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    raw = tmp_path / "raw" / "fleet"
    raw.mkdir(parents=True)
    (raw / "context.json").write_text(json.dumps({"known": _KNOWN}), encoding="utf-8")
    (raw / "happ.json").write_text(json.dumps(_raw(_LINKS, [["content-type", "text/plain"]], encode=True)))
    golden = tmp_path / "golden"
    assert main(["normalize", str(tmp_path / "raw"), "--out", str(golden)]) == 0
    assert main(["compare", str(tmp_path / "raw"), "--golden", str(golden)]) == 0
    assert isinstance(load_corpus(golden)[("fleet", "happ")], Capture)
    extra = json.loads((golden / "fleet" / "happ.json").read_text())
    extra["request"]["id"] = "curl"
    (golden / "fleet" / "curl.json").write_text(json.dumps(extra))
    assert main(["compare", str(tmp_path / "raw"), "--golden", str(golden)]) == 1
    assert "fleet/curl:\n  not captured" in capsys.readouterr().out
