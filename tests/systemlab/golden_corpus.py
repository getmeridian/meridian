"""Golden subscription corpus: normalize System Lab captures, store fixtures, compare.

The corpus was captured once from Remnawave, as raw answers (live secrets included) under
``output/golden-raw/<topology>/``, and is now frozen. This module turned them into fixtures
under ``golden/<topology>/``: one JSON file per request (status, format, headers, matched
rule) pointing at a deduplicated, normalized body in ``golden/<topology>/bodies/``.
``scripts/diff-golden.py`` normalizes live control-service answers the same way.

Normalization replaces every secret and every random value with ``<kind:N>``, numbered per
kind in order of first appearance (body first, then headers, in one numbering scope), and
lab addresses with ``<host:role>``. It is deterministic and idempotent: normalizing a
fixture again changes nothing. Base64 bodies are decoded first and stored decoded.

    uv run python -m tests.systemlab.golden_corpus normalize tests/systemlab/output/golden-raw
    uv run python -m tests.systemlab.golden_corpus compare tests/systemlab/output/golden-raw
"""

from __future__ import annotations

import argparse
import base64
import binascii
import difflib
import hashlib
import json
import re
import shutil
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

GOLDEN_DIR = Path(__file__).parent / "golden"
RAW_DIR = Path(__file__).parent / "output" / "golden-raw"

# Connection-scoped headers (RFC 9110 section 7.6.1) never belong to a subscription answer.
HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
# Headers whose values differ on every request; stored as ``<volatile>`` and never compared.
VOLATILE_HEADERS = frozenset({"date", "etag", "last-modified", "expires", "age", "content-length", "x-request-id"})
VOLATILE = "<volatile>"

# Lab addresses become role placeholders, so fixtures never depend on the lab layout.
LAB_HOST_ROLES = {
    "EXIT_A_IP": "exit-a",
    "EXIT_B_IP": "exit-b",
    "RELAY_A_IP": "relay-a",
    "RELAY_B_IP": "relay-b",
    "GATEWAY_IP": "gateway-a",
}

TOPOLOGIES = {
    "single": "one exit deployed by `meridian deploy` (exit B is control and exit)",
    "fleet": "two exits in a leastPing pool, a two-hop Realm relay chain to exit A, and a routing gateway",
    "reduced": "the fleet after removing the relay chain and exit B: exit A and the routing gateway",
}

_TOKEN = re.compile("\x00(\\d+)\x00")
_NUMBERED = re.compile(r"<([a-z][a-z0-9-]*):(\d+)>")
_UUID = re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}(?![0-9A-Za-z])")
_LAB_IP = re.compile(r"(?<![0-9.])172\.30\.0\.\d{1,3}(?![0-9])")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})")
# A secret is never part of a longer word; a percent escape before it (``%2F``, ``%22``) is
# a delimiter, so values inside URL-encoded paths and JSON (Hysteria2's ``fm``) still match.
_BEFORE = r"(?:(?<=%[0-9A-Fa-f]{2})|(?<![A-Za-z0-9_\-]))"
_AFTER = r"(?![A-Za-z0-9_\-])"
# Field names that carry secrets in links (query), Xray / sing-box JSON, and Mihomo YAML.
_SECRET_FIELDS = {
    "pbk": "reality-pbk",
    "publicKey": "reality-pbk",
    "public_key": "reality-pbk",
    "public-key": "reality-pbk",
    "sid": "short-id",
    "shortId": "short-id",
    "short_id": "short-id",
    "short-id": "short-id",
    "password": "password",
    "obfs-password": "password",
    "obfsPassword": "password",
}
_FIELD_NAMES = "|".join(re.escape(name) for name in sorted(_SECRET_FIELDS, key=len, reverse=True))
_FIELD_PATTERNS = (
    re.compile(rf'(?P<pre>"(?P<key>{_FIELD_NAMES})"\s*:\s*")(?P<value>[^"\x00<]+)(?=")'),
    re.compile(rf"(?P<pre>[?&](?P<key>{_FIELD_NAMES})=)(?P<value>[^&#\s\x00<]+)"),
    re.compile(rf"(?P<pre>^[ \t-]*(?P<key>{_FIELD_NAMES}):[ \t]*['\"]?)(?P<value>[^\s'\",}}\x00<]+)", re.MULTILINE),
)
_USERINFO_COUNTERS = re.compile(r"\b(upload|download)=\d+")
# Traffic counters and days left in the browser's subscription-info JSON change with time.
_VOLATILE_FIELDS = re.compile(
    r'"(?P<key>daysLeft|trafficUsed|trafficUsedBytes|lifetimeTrafficUsed|lifetimeTrafficUsedBytes)":'
    r'(?:"[^"]*"|-?\d+(?:\.\d+)?)'
)


def _known_pattern(value: str) -> re.Pattern[str]:
    variants = sorted({value, quote(value, safe=""), quote(value)}, key=len, reverse=True)
    return re.compile(_BEFORE + "(?:" + "|".join(re.escape(variant) for variant in variants) + ")" + _AFTER)


@dataclass
class Normalizer:
    """One numbering scope: placeholders per kind, numbered in order of first appearance.

    ``mark`` swaps a value for an opaque NUL-delimited index (so later patterns never see
    the value again); ``finish`` turns the indexes into ``<kind:N>`` in text order.
    """

    known: Sequence[tuple[str, str]] = ()
    marks: list[tuple[str, str]] = field(default_factory=list)
    numbers: dict[tuple[str, str], int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    def mark(self, kind: str, identity: str) -> str:
        self.marks.append((kind, identity))
        return f"\x00{len(self.marks) - 1}\x00"

    def normalize(self, text: str) -> str:
        text = _NUMBERED.sub(lambda match: self.mark(match.group(1), "#" + match.group(2)), text)
        for kind, value in sorted(self.known, key=lambda pair: len(pair[1]), reverse=True):
            if kind.startswith("host:"):
                text = _known_pattern(value).sub(lambda _match, fixed=f"<{kind}>": fixed, text)
            else:
                text = _known_pattern(value).sub(lambda _match, k=kind, v=value: self.mark(k, v), text)
        text = _UUID.sub(lambda match: self.mark("uuid", match.group(0).lower()), text)
        for pattern in _FIELD_PATTERNS:
            text = pattern.sub(
                lambda match: match.group("pre") + self.mark(_SECRET_FIELDS[match.group("key")], match.group("value")),
                text,
            )
        text = _VOLATILE_FIELDS.sub(lambda match: f'"{match.group("key")}":"{VOLATILE}"', text)
        text = _LAB_IP.sub(lambda match: self.mark("lab-ip", match.group(0)), text)
        text = _TIMESTAMP.sub(lambda match: self.mark("timestamp", match.group(0)), text)
        return _TOKEN.sub(self._number, text)

    def _number(self, match: re.Match[str]) -> str:
        key = self.marks[int(match.group(1))]
        if key not in self.numbers:
            self.counts[key[0]] = self.counts.get(key[0], 0) + 1
            self.numbers[key] = self.counts[key[0]]
        return f"<{key[0]}:{self.numbers[key]}>"


def decode_base64_text(body: bytes) -> str | None:
    """The decoded link list when *body* is exactly its standard base64, else None.

    Only canonical encodings count, so a fixture's decoded text re-encodes to the served bytes.
    """
    try:
        decoded = base64.b64decode(body, validate=True)
        text = decoded.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    return text if "://" in text and base64.b64encode(decoded) == body else None


def detect_format(content_type: str, text: str, *, encoding: str = "identity") -> str:
    """Name the document kind a client received (the matched rule says which template)."""
    if encoding == "base64":
        return "base64"
    if not text.strip():
        return "empty"
    if "text/html" in content_type:
        return "html"
    try:
        document = json.loads(text)
    except ValueError:
        document = None
    if isinstance(document, list) and document and all(isinstance(item, dict) for item in document):
        return "xray-json" if all("outbounds" in item for item in document) else "json"
    if isinstance(document, dict):
        outbounds = document.get("outbounds")
        if isinstance(outbounds, list) and outbounds:
            return "singbox" if all("type" in item for item in outbounds) else "xray-json"
        return "json"
    if "yaml" in content_type:
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError:
            return "text"
        return "clash-yaml" if isinstance(parsed, dict) and "proxies" in parsed else "yaml"
    return "text"


@dataclass(frozen=True)
class Response:
    """A normalized answer: what a fixture stores and what the comparator diffs."""

    status: int
    format: str
    encoding: str  # "base64" when the served body was base64 of ``body``
    headers: tuple[tuple[str, str], ...]
    body: str

    def header(self, name: str) -> str:
        return next((value for key, value in self.headers if key == name), "")


def _normalize_header(name: str, value: str, normalizer: Normalizer) -> str:
    if name in VOLATILE_HEADERS:
        return VOLATILE
    if name == "subscription-userinfo":
        value = _USERINFO_COUNTERS.sub(lambda match: f"{match.group(1)}=<bytes>", value)
    return normalizer.normalize(value)


def normalize_response(
    status: int,
    headers: Iterable[Sequence[str]],
    body: str,
    *,
    encoding: str = "identity",
    known: Sequence[tuple[str, str]] = (),
) -> Response:
    """Normalize one answer whose *body* is already decoded text (see ``decode_body``)."""
    pairs = [(name.lower(), value) for name, value in headers if name.lower() not in HOP_BY_HOP_HEADERS]
    normalizer = Normalizer(known)
    text = normalizer.normalize(body)
    normalized = tuple((name, _normalize_header(name, value, normalizer)) for name, value in pairs)
    content_type = next((value for name, value in pairs if name == "content-type"), "")
    return Response(status, detect_format(content_type, text, encoding=encoding), encoding, normalized, text)


def decode_body(content_type: str, raw: bytes) -> tuple[str, str]:
    """Return ``(encoding, text)``: base64 link lists decode, everything else is UTF-8."""
    if "text/plain" in content_type and (decoded := decode_base64_text(raw)) is not None:
        return "base64", decoded
    return "identity", raw.decode("utf-8")


@dataclass(frozen=True)
class Capture:
    """One recorded request and its normalized answer."""

    topology: str
    request: dict[str, Any]
    expected: dict[str, Any] | None
    matched: dict[str, Any] | None
    response: Response

    @property
    def key(self) -> tuple[str, str]:
        return self.topology, str(self.request["id"])


def _expected(agent_id: str, selector: str) -> dict[str, Any] | None:
    from tests.systemlab.golden_agents import AGENTS_BY_ID

    agent = AGENTS_BY_ID.get(agent_id)
    if agent is None or selector:
        return None
    return {"rule": agent.expected_rule, "response_type": agent.expected_type}


def normalize_raw(raw: Mapping[str, Any], known: Sequence[tuple[str, str]]) -> Capture:
    """Normalize one raw capture file in the capture format."""
    from tests.systemlab.golden_agents import AGENTS_BY_ID

    request = dict(raw["request"])
    response = raw["response"]
    headers = [tuple(pair) for pair in response["headers"]]
    content_type = next((value for name, value in headers if name.lower() == "content-type"), "")
    encoding, text = decode_body(content_type, base64.b64decode(response["body_b64"]))
    agent = AGENTS_BY_ID.get(str(request["agent"]))
    request["app"] = agent.app if agent is not None else ""
    return Capture(
        topology=str(raw["topology"]),
        request={key: request[key] for key in ("id", "agent", "app", "user", "selector", "user_agent", "accept")},
        expected=_expected(str(request["agent"]), str(request.get("selector", ""))),
        matched=raw.get("matched"),
        response=normalize_response(int(response["status"]), headers, text, encoding=encoding, known=known),
    )


def read_raw_corpus(raw_dir: Path) -> tuple[dict[tuple[str, str], Capture], dict[str, dict[str, Any]]]:
    """Normalize every raw capture below *raw_dir*; also return each topology's context."""
    captures: dict[tuple[str, str], Capture] = {}
    contexts: dict[str, dict[str, Any]] = {}
    for topology_dir in sorted(path for path in raw_dir.iterdir() if path.is_dir()):
        context = json.loads((topology_dir / "context.json").read_text(encoding="utf-8"))
        known = [(str(kind), str(value)) for kind, value in context.get("known", [])]
        contexts[topology_dir.name] = context
        for path in sorted(topology_dir.glob("*.json")):
            if path.name == "context.json":
                continue
            capture = normalize_raw(json.loads(path.read_text(encoding="utf-8")), known)
            captures[capture.key] = capture
    return captures, contexts


_EXTENSIONS = {
    "base64": "txt",
    "text": "txt",
    "xray-json": "json",
    "singbox": "json",
    "json": "json",
    "clash-yaml": "yaml",
    "yaml": "yaml",
}


def _compact_json(text: str) -> str | None:
    try:
        document = json.loads(text)
    except ValueError:
        return None
    return (
        json.dumps(document, ensure_ascii=False, separators=(",", ":")) if isinstance(document, dict | list) else None
    )


def stored_body(text: str) -> tuple[str, str]:
    """Return ``(layout, file text)``: compact JSON is stored indented, so diffs stay readable.

    Only a body that re-serializes to exactly its served bytes is reflowed; ``served_body``
    turns the stored text back into those bytes.
    """
    if _compact_json(text) == text:
        return "compact-json", json.dumps(json.loads(text), indent=2, ensure_ascii=False) + "\n"
    return "verbatim", text


def served_body(stored: str, layout: str) -> str:
    if layout == "compact-json":
        return json.dumps(json.loads(stored), ensure_ascii=False, separators=(",", ":"))
    return stored


def body_file_name(response: Response) -> str:
    digest = hashlib.sha256(response.body.encode("utf-8")).hexdigest()
    return f"bodies/{response.format}-{digest[:12]}.{_EXTENSIONS.get(response.format, response.format)}"


def _fixture(capture: Capture) -> dict[str, Any]:
    response = capture.response
    body = None
    if response.body:
        body = {
            "encoding": response.encoding,
            "layout": stored_body(response.body)[0],
            "file": body_file_name(response),
            "sha256": hashlib.sha256(response.body.encode("utf-8")).hexdigest(),
        }
    return {
        "topology": capture.topology,
        "request": capture.request,
        "expected": capture.expected,
        "matched": capture.matched,
        "response": {
            "status": response.status,
            "format": response.format,
            "headers": [list(pair) for pair in response.headers],
            "body": body,
        },
    }


def leaked_kinds(response: Response, known: Sequence[tuple[str, str]]) -> list[str]:
    """Kinds of *known* values that survived normalization (any spelling, anywhere)."""
    text = response.body + json.dumps(response.headers, ensure_ascii=False)
    return sorted(
        {
            kind
            for kind, value in known
            if any(form in text for form in {value, quote(value, safe="")}) and len(value) >= 6
        }
    )


_PAIR = re.compile(r'\[\n\s+("(?:[^"\\]|\\.)*"),\n\s+("(?:[^"\\]|\\.)*")\n\s+\]')


# The Remnawave release the corpus was captured from; links it served must keep working after
# `meridian migrate`, though Meridian no longer deploys a panel to capture again.
CAPTURED_BACKEND = "remnawave/backend:3.4.5@sha256:b16d724b90fd7c9fec2df04bd28938a671cafc62894105068e11550ee3449c56"


def _dump(payload: Any) -> str:
    """Indented JSON with each header pair on one line."""
    return _PAIR.sub(r"[\1, \2]", json.dumps(payload, indent=2, ensure_ascii=False)) + "\n"


def write_corpus(
    captures: Mapping[tuple[str, str], Capture], contexts: Mapping[str, Mapping[str, Any]], out: Path
) -> None:
    """Replace *out* with fixtures, deduplicated bodies, and the manifest."""
    from tests.systemlab.golden_agents import AGENTS

    for capture in captures.values():
        known = [(str(kind), str(value)) for kind, value in contexts[capture.topology].get("known", [])]
        if leaked := leaked_kinds(capture.response, known):
            raise ValueError(f"{capture.topology}/{capture.request['id']} still holds {', '.join(leaked)}")
    for topology in contexts:
        shutil.rmtree(out / topology, ignore_errors=True)
    for capture in captures.values():
        topology_dir = out / capture.topology
        fixture = _fixture(capture)
        (topology_dir / "bodies").mkdir(parents=True, exist_ok=True)
        (topology_dir / f"{capture.request['id']}.json").write_text(_dump(fixture), encoding="utf-8")
        if fixture["response"]["body"] is not None:
            stored = stored_body(capture.response.body)[1]
            (topology_dir / fixture["response"]["body"]["file"]).write_text(stored, encoding="utf-8")
    first = next(iter(contexts.values()))
    manifest = {
        "remnawave_backend": CAPTURED_BACKEND,
        "meridian_version": first.get("meridian_version", ""),
        "serve_json_at_base_subscription": first.get("serve_json_at_base_subscription"),
        "response_rules": first.get("response_rules"),
        "topologies": {name: TOPOLOGIES.get(name, "") for name in sorted(contexts)},
        "agents": [
            {
                "id": agent.id,
                "app": agent.app,
                "user_agent": agent.user_agent,
                "accept": agent.accept,
                "expected_rule": agent.expected_rule,
                "expected_type": agent.expected_type,
            }
            for agent in AGENTS
        ],
    }
    (out / "manifest.json").write_text(_dump(manifest), encoding="utf-8")


def load_corpus(golden_dir: Path = GOLDEN_DIR) -> dict[tuple[str, str], Capture]:
    """Read every committed fixture with its body."""
    captures: dict[tuple[str, str], Capture] = {}
    for path in sorted(golden_dir.glob("*/*.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        response = fixture["response"]
        body_ref = response["body"]
        text = ""
        if body_ref:
            text = served_body((path.parent / body_ref["file"]).read_text(encoding="utf-8"), body_ref["layout"])
        capture = Capture(
            topology=fixture["topology"],
            request=fixture["request"],
            expected=fixture["expected"],
            matched=fixture["matched"],
            response=Response(
                status=response["status"],
                format=response["format"],
                encoding=body_ref["encoding"] if body_ref else "identity",
                headers=tuple((name, value) for name, value in response["headers"]),
                body=text,
            ),
        )
        captures[capture.key] = capture
    return captures


def _same_document(left: str, right: str) -> bool:
    for parse in (json.loads, yaml.safe_load):
        try:
            return bool(parse(left) == parse(right))
        except (ValueError, yaml.YAMLError):
            continue
    return False


def compare_response(
    golden: Response, candidate: Response, *, ignore_headers: frozenset[str] = VOLATILE_HEADERS
) -> list[str]:
    """Differences between two normalized answers; empty means equivalent."""
    problems: list[str] = []
    if golden.status != candidate.status:
        problems.append(f"status {candidate.status} != {golden.status}")
    if golden.format != candidate.format or golden.encoding != candidate.encoding:
        problems.append(f"format {candidate.format}/{candidate.encoding} != {golden.format}/{golden.encoding}")
    wanted = sorted(pair for pair in golden.headers if pair[0] not in ignore_headers)
    served = sorted(pair for pair in candidate.headers if pair[0] not in ignore_headers)
    for name, value in sorted(set(wanted) - set(served)):
        problems.append(f"header missing or changed: {name}: {value}")
    for name, value in sorted(set(served) - set(wanted)):
        problems.append(f"header unexpected: {name}: {value}")
    if golden.body != candidate.body:
        if _same_document(golden.body, candidate.body):
            problems.append("body: same document, different bytes (formatting or key order)")
        else:
            diff = difflib.unified_diff(
                golden.body.splitlines(), candidate.body.splitlines(), "golden", "candidate", lineterm="", n=1
            )
            problems.append("body differs:\n" + "\n".join(list(diff)[:40]))
    return problems


def compare_corpus(
    golden: Mapping[tuple[str, str], Capture], candidate: Mapping[tuple[str, str], Capture]
) -> dict[tuple[str, str], list[str]]:
    """Per-request differences, restricted to the topologies *candidate* covers."""
    topologies = {topology for topology, _request in candidate}
    report: dict[tuple[str, str], list[str]] = {}
    for key in sorted(set(golden) | set(candidate)):
        if key[0] not in topologies:
            continue
        if key not in candidate:
            report[key] = ["not captured"]
        elif key not in golden:
            report[key] = ["no golden fixture"]
        elif problems := compare_response(golden[key].response, candidate[key].response):
            report[key] = problems
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    normalize = commands.add_parser("normalize", help="write fixtures from raw captures")
    normalize.add_argument("raw", type=Path, nargs="?", default=RAW_DIR)
    normalize.add_argument("--out", type=Path, default=GOLDEN_DIR)
    compare = commands.add_parser("compare", help="diff raw captures against the fixtures")
    compare.add_argument("raw", type=Path, nargs="?", default=RAW_DIR)
    compare.add_argument("--golden", type=Path, default=GOLDEN_DIR)
    args = parser.parse_args(argv)

    captures, contexts = read_raw_corpus(args.raw)
    if args.command == "normalize":
        write_corpus(captures, contexts, args.out)
        print(f"wrote {len(captures)} fixtures for {', '.join(sorted(contexts))} to {args.out}")
        return 0
    report = compare_corpus(load_corpus(args.golden), captures)
    for (topology, request_id), problems in report.items():
        print(f"{topology}/{request_id}:")
        for problem in problems:
            print("  " + problem.replace("\n", "\n  "))
    print(f"{len(captures) - len(report)} of {len(captures)} captures match the golden corpus")
    return 1 if report else 0


if __name__ == "__main__":
    sys.exit(main())
