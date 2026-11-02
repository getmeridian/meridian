"""Pure upgrade-verifier decisions; no network, subprocesses, or local state."""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from packaging.version import InvalidVersion, Version

from meridian.control.formats.selector import DocumentFormat, select_format
from meridian.core.clients import ClientDetail
from meridian.core.redaction import redact_diagnostic_text
from meridian.setup.single_server import single_server_intent
from tests.support.text import unstyle


@dataclass(frozen=True)
class SubscriptionCase:
    name: str
    user_agent: str

    @property
    def format(self) -> DocumentFormat:
        return select_format(self.user_agent, None)

    @property
    def label(self) -> str:
        return f"{self.format} ({self.name})"


SUBSCRIPTION_CASES = (
    SubscriptionCase("v2rayNG", "v2rayNG/1.10.20"),
    SubscriptionCase("Happ", "Happ/1.0"),
    SubscriptionCase("clash-verge", "clash-verge/v2.2"),
    SubscriptionCase("Stash", "Stash/3.0"),
    SubscriptionCase("SFA", "SFA/1.12.0"),
)
USERINFO_FIELDS = ("upload", "download", "total", "expire")
_URI = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s\"'<>]+", re.IGNORECASE)
_API_TOKEN = re.compile(r"\bmrd_[0-9a-f]{12}_[0-9a-f]{64}\b", re.IGNORECASE)
_SECRET_PATH = re.compile(r"/[0-9a-f]{16,32}(?=/|$|[?#\s\"'])", re.IGNORECASE)
_IPV4 = re.compile(r"(?<![\w.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![\w.])")
_DASHED_IP = re.compile(r"(?<![\w-])(?:[0-9]{1,3}-){3}[0-9]{1,3}(?![\w-])")
_IPV6 = re.compile(r"(?<![\w:])(?:[0-9a-f]{0,4}:){2,}[0-9a-f:.]+(?:%[\w.-]+)?", re.IGNORECASE)
_HOSTNAME = re.compile(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,63}\b", re.IGNORECASE)
_FILE_EXTENSIONS = {"sh", "py", "md", "json", "yaml", "yml", "log", "conf", "db", "version", "pem"}


def previous_published_version(checkout: str, releases: Sequence[Version], override: str = "") -> str:
    """Newest published version strictly below VERSION, including betas and release candidates.

    An explicit pin may equal VERSION: a checkout can have unreleased changes under the
    published beta's version. Pins never name local builds.
    """
    try:
        current = Version(checkout.strip())
    except InvalidVersion:
        raise ValueError("The checkout's VERSION is not a valid package version.") from None
    if override.strip():
        try:
            pinned = Version(override.strip())
        except InvalidVersion:
            raise ValueError("REALVM_UPGRADE_FROM must be an exact published package version.") from None
        if pinned.local:
            raise ValueError("REALVM_UPGRADE_FROM must name a published release, not a local build.")
        return str(pinned)
    previous = max((version for version in releases if version < current), default=None)
    if previous is None:
        raise ValueError(
            "No published release is older than the checkout's VERSION. Set REALVM_UPGRADE_FROM explicitly."
        )
    return str(previous)


def build_intent(server_ref: str, ip: str) -> str:
    """Single exit on the bare IP, default camouflage SNI and privacy settings."""
    ipaddress.IPv4Address(ip)
    return single_server_intent(None, server_ref=server_ref, address=ip).model_dump_json(indent=2)


def confirmation_input(help_text: str, *, required: Sequence[str] = ()) -> tuple[list[str], str | None]:
    """Use --yes only when the installed release advertises it; otherwise answer stdin."""
    options = set(re.findall(r"--[a-z][a-z-]*", unstyle(help_text)))
    if any(option not in options for option in required):
        raise ValueError("The selected release does not support the required client limit and expiry options.")
    return (["--yes"], None) if "--yes" in options else ([], "y\n")


def command_data(output: str) -> dict[str, Any]:
    """Read data from a JSON envelope; never echo credential-bearing malformed output."""
    try:
        start = output.index("{")
        value, _ = json.JSONDecoder().raw_decode(output[start:])
        if isinstance(value, dict) and isinstance(value.get("data"), dict):
            return value["data"]
    except (ValueError, TypeError):
        pass
    raise ValueError("The command did not return a JSON envelope with object data.")


def client_detail(data: Mapping[str, Any]) -> ClientDetail:
    """Validate the shared client contract without displaying handoff links."""
    try:
        return ClientDetail.model_validate(data["client"])
    except (ValueError, KeyError, TypeError):
        raise ValueError("Client show did not return valid client metadata.") from None


def fleet_agent_version(data: Mapping[str, Any]) -> str:
    """One connected node's fresh agent version, from fleet status runtime evidence."""
    try:
        nodes = data["nodes"]
        if len(nodes) == 1 and nodes[0]["status"] == "connected" and nodes[0].get("runtime_available") is True:
            version = nodes[0]["runtime"]["versions"]["agent"]
            if isinstance(version, str) and version:
                return version
    except (KeyError, TypeError):
        pass
    raise ValueError("Fleet status has no fresh agent version for the connected exit.")


@dataclass(frozen=True)
class SubscriptionSnapshot:
    body: bytes
    userinfo: str


def parse_userinfo(header: str) -> dict[str, int]:
    """Require all four nonnegative counters; an absent or malformed header cannot pass."""
    values: dict[str, int] = {}
    for item in header.split(";"):
        key, separator, raw = item.strip().partition("=")
        if not separator or key not in USERINFO_FIELDS or key in values or not raw.isascii() or not raw.isdigit():
            raise ValueError("The subscription-userinfo header is missing or malformed.")
        values[key] = int(raw)
    if set(values) != set(USERINFO_FIELDS):
        raise ValueError("The subscription-userinfo header is missing or malformed.")
    return values


def document_diff_summary(before: bytes, after: bytes) -> str:
    """Content-free byte diff: never excerpts or hashes of documents, even Base64 ones."""
    first = next(
        (offset for offset, (left, right) in enumerate(zip(before, after)) if left != right),
        min(len(before), len(after)),
    )
    return (
        f"{len(before)} -> {len(after)} bytes; first difference at byte {first}; "
        f"lines {len(before.splitlines())} -> {len(after.splitlines())}"
    )


def compare_subscriptions(
    before: Mapping[tuple[str, str], SubscriptionSnapshot],
    after: Mapping[tuple[str, str], SubscriptionSnapshot],
) -> list[str]:
    """Compare every person/format and the exact header, including fixed expire= values."""
    if not before:
        raise ValueError("No subscriptions were captured before the upgrade.")
    failures = []
    for key in sorted(before.keys() | after.keys()):
        label = redact_output(f"{key[0]} / {key[1]}").replace("\n", " ")[:100]
        if key not in before or key not in after:
            failures.append(f"{label}: subscription missing {'before' if key not in before else 'after'} upgrade")
            continue
        old, new = before[key], after[key]
        if old.body != new.body:
            failures.append(f"{label}: document differs ({document_diff_summary(old.body, new.body)})")
        old_info, new_info = parse_userinfo(old.userinfo), parse_userinfo(new.userinfo)
        if old.userinfo != new.userinfo:
            changed = [field for field in USERINFO_FIELDS if old_info[field] != new_info[field]]
            failures.append(f"{label}: subscription-userinfo differs ({', '.join(changed) or 'header bytes'})")
    return failures


def redact_output(text: str, known: Iterable[str] = ()) -> str:
    """Extend Meridian's diagnostic redaction with lab addresses and opaque handoff URIs."""
    for value in sorted({value for value in known if value}, key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    text = _URI.sub("[REDACTED]", text)
    text = redact_diagnostic_text(text)
    text = _API_TOKEN.sub("[REDACTED]", text)
    text = _SECRET_PATH.sub("/[REDACTED]", text)
    text = _HOSTNAME.sub(
        lambda match: match.group() if match.group().rsplit(".", 1)[-1].lower() in _FILE_EXTENSIONS else "[HOST]",
        text,
    )

    def mask_address(match: re.Match[str]) -> str:
        raw = match.group().split("%", 1)[0].replace("-", ".")
        try:
            ipaddress.ip_address(raw)
        except ValueError:
            return match.group()
        return "[IP]"

    for pattern in (_IPV6, _IPV4, _DASHED_IP):
        text = pattern.sub(mask_address, text)
    return text


def _nested_errors(value: object, path: str) -> list[tuple[str, str]]:
    if isinstance(value, dict):
        label = value.get("logical_id") or path
        own = [(str(label), str(value["error"]))] if isinstance(value.get("error"), str) and value["error"] else []
        return own + [item for key, child in value.items() for item in _nested_errors(child, f"{path}.{key}")]
    if isinstance(value, list):
        return [item for index, child in enumerate(value) for item in _nested_errors(child, f"{path}[{index}]")]
    return []


def failure_excerpt(output: str, known: Iterable[str] = (), *, lines: int = 25, width: int = 300) -> str:
    """A failed command's errors (JSON envelopes) or redacted tail, so a lab failure names its cause."""
    try:
        envelope = json.loads(output.split("\n\n", 1)[0] if output.lstrip().startswith("{") else "")
    except ValueError:
        envelope = None
    if isinstance(envelope, dict):
        found = [f"{item.get('code', 'error')}: {item.get('message', '')}" for item in envelope.get("errors") or []]
        found += [f"{path}: {error}" for path, error in _nested_errors(envelope.get("data"), "data")]
        if found:
            output = "\n".join(found)
    tail = [line[:width] for line in redact_output(output, known).splitlines() if line.strip()][-lines:]
    return "\n".join(f"    | {line}" for line in tail)


def is_pypi_unreachable(output: str) -> bool:
    """Installer transport failures are external; an unavailable version is a refusal."""
    lowered = output.lower()
    transport = (
        "failed to fetch",
        "error sending request",
        "connection refused",
        "network is unreachable",
        "dns error",
        "timed out",
        "certificate verify failed",
    )
    return any(marker in lowered for marker in transport) and any(host in lowered for host in ("pypi", "pythonhosted"))
