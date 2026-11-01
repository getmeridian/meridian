"""`tests/realvm/domain_support.py`: what the `domain` verifier decides without a server.

The verifier itself needs a real VM and never runs in CI; its pure decisions do.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from meridian.core.topology import SetupIntent
from meridian.provision.tls import _rate_limit_hint
from meridian.servers import ServerEntry, ServerRegistry
from meridian.setup.fleet_document import (
    dump_fleet_document,
    fleet_document,
    intent_from_document,
    parse_fleet_document,
)
from tests.realvm import domain_support as support

IP = "203.0.113.10"
PANEL = "203-0-113-10.sslip.io"


@pytest.fixture
def registry(tmp_path: Path) -> ServerRegistry:
    shelf = ServerRegistry(tmp_path / "servers.json")
    shelf.add(ServerEntry(host=IP))
    return shelf


def _paths(intent: SetupIntent) -> dict[str, tuple[str, str]]:
    return {path.protocol: (path.tls_sni, path.host) for path in intent.exits[0].paths}


@pytest.mark.parametrize(
    ("ip", "override", "expected"),
    [
        (IP, "", PANEL),
        (IP, "  Panel.Example.org. ", "panel.example.org"),
        ("198.51.100.7", "", "198-51-100-7.sslip.io"),
    ],
    ids=["sslip-default", "override-normalized", "other-address"],
)
def test_panel_domain_defaults_to_the_sslip_name_and_honours_an_override(ip: str, override: str, expected: str) -> None:
    assert support.panel_domain(ip, override) == expected


@pytest.mark.parametrize(
    ("ip", "override"),
    [("2001:db8::1", ""), ("not-an-address", ""), (IP, "-bad_name"), (IP, "https://panel.example.org")],
    ids=["ipv6", "garbage-ip", "bad-override", "url-override"],
)
def test_panel_domain_refuses_what_dns_cannot_name(ip: str, override: str) -> None:
    with pytest.raises(ValueError):
        support.panel_domain(ip, override)


def test_the_ip_paths_intent_puts_the_panel_on_the_domain_and_the_exit_on_the_ip() -> None:
    intent = SetupIntent.model_validate_json(support.build_intent("srv-aaaaaaaaaaaa", IP, PANEL))

    assert intent.control.public_hostname == PANEL
    assert intent.access.users == ["alice"]
    assert _paths(intent) == {"reality": ("", ""), "xhttp": (IP, IP), "hysteria2": (IP, "")}


def test_the_shared_intent_puts_the_exit_on_the_domain_too() -> None:
    intent = SetupIntent.model_validate_json(support.build_intent("srv-aaaaaaaaaaaa", IP, PANEL, shared=True))

    assert intent.control.public_hostname == PANEL
    assert _paths(intent)["xhttp"] == (PANEL, PANEL)
    assert _paths(intent)["hysteria2"] == (PANEL, "")


def test_switching_an_exported_fleet_moves_only_the_ip_paths_and_imports_cleanly(registry: ServerRegistry) -> None:
    ref = registry.find(IP).id  # type: ignore[union-attr]
    current = SetupIntent.model_validate_json(support.build_intent(ref, IP, PANEL))
    exported = dump_fleet_document(fleet_document(current, registry))

    switched = support.switch_exit_paths(exported, IP, PANEL)
    updated = intent_from_document(parse_fleet_document(switched), current, registry)

    assert _paths(updated) == {"reality": ("", ""), "xhttp": (PANEL, PANEL), "hysteria2": (PANEL, "")}
    assert updated.exits[0].paths[0].reality_sni == current.exits[0].paths[0].reality_sni
    assert updated.control == current.control


def test_switching_a_fleet_with_no_ip_path_is_an_error_not_a_silent_pass(registry: ServerRegistry) -> None:
    ref = registry.find(IP).id  # type: ignore[union-attr]
    already = support.build_intent(ref, IP, PANEL, shared=True)
    exported = dump_fleet_document(fleet_document(SetupIntent.model_validate_json(already), registry))

    with pytest.raises(ValueError, match="nothing to switch"):
        support.switch_exit_paths(exported, IP, PANEL)


def test_counts_are_read_from_an_envelope_that_follows_a_notice() -> None:
    envelope = {"summary": {"counts": {"checks": 7, "passed": 4, "failed": 1, "warnings": 1, "skipped": 1}}}
    output = "Backed up the snapshot.\n" + json.dumps(envelope, indent=2)

    assert support.verification_counts(output) == (4, 1, 1, 1)


@pytest.mark.parametrize(
    "output", ["", "no braces here", "[1, 2]", '{"summary": {}}'], ids=["empty", "text", "list", "bare"]
)
def test_counts_refuse_output_without_an_envelope(output: str) -> None:
    with pytest.raises((ValueError, KeyError)):
        support.verification_counts(output)


@pytest.mark.parametrize(
    "output",
    [
        _rate_limit_hint("... urn:ietf:params:acme:error:rateLimited ... retry after 2026-11-02 10:00:00 UTC", PANEL),
        _rate_limit_hint("urn:ietf:params:acme:error:rateLimited", IP),
        "Error creating new order :: too many certificates (5) already issued ... retry after 2026-11-02 10:00:00 UTC",
        "acme: urn:ietf:params:acme:error:rateLimited",
    ],
    ids=["meridian-with-time", "meridian-without-time", "acme-retry-after", "acme-problem-type"],
)
def test_a_lets_encrypt_rate_limit_is_recognised_in_meridians_and_acmes_words(output: str | None) -> None:
    assert output is not None
    assert support.is_rate_limited(output)


@pytest.mark.parametrize(
    "output",
    [
        "Failed to issue a trusted TLS certificate for 203-0-113-10.sslip.io: DNS problem. Check port 80",
        "Connection to 203.0.113.10 timed out",
        "",
    ],
    ids=["issuance-failure", "ssh", "empty"],
)
def test_other_failures_are_not_mistaken_for_a_rate_limit(output: str) -> None:
    assert not support.is_rate_limited(output)


def test_the_cli_exit_codes_tell_a_rate_limit_apart(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("urn:ietf:params:acme:error:rateLimited"))
    assert support.main(["rate-limited"]) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO("fine"))
    assert support.main(["rate-limited"]) == 1
    assert support.INCONCLUSIVE_EXIT not in (0, 1, 2)

    monkeypatch.setenv(support.PANEL_DOMAIN_ENV, "panel.example.org")
    assert support.main(["panel-domain", IP]) == 0
    assert capsys.readouterr().out.strip() == "panel.example.org"
    monkeypatch.delenv(support.PANEL_DOMAIN_ENV)
    assert support.main(["panel-domain", "not-an-address"]) == 2
