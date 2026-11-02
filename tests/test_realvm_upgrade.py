"""Upgrade-verifier decisions run in CI; cloud provisioning never does."""

from __future__ import annotations

import base64
import json

import pytest
from packaging.version import Version

from meridian.core.topology import SetupIntent
from tests.realvm import upgrade_support as support

IP = "198.51.100.10"
HEADER = "upload=12; download=34; total=50000000000; expire=1893456000"


@pytest.mark.parametrize(
    ("checkout", "published", "expected"),
    [
        ("4.0.0b10", ["3.17.3", "4.0.0b9", "4.0.0b10", "4.0.0rc1"], "4.0.0b9"),
        ("4.0.0", ["3.17.3", "4.0.0b10", "4.0.0rc2", "4.0.0"], "4.0.0rc2"),
        ("4.0.0b11", ["4.0.0b10", "4.0.0b9", "4.0.0b2"], "4.0.0b10"),
        ("4.1.0", ["4.0.0", "4.0.1.post1", "4.1.0b1"], "4.1.0b1"),
        ("4.0.0b10", ["4.0.0b9", "4.0.0b10.dev2"], "4.0.0b10.dev2"),
    ],
    ids=["strictly-below", "release-candidate", "numeric-beta-order", "includes-prereleases", "published-dev-release"],
)
def test_previous_release_is_the_newest_published_version_strictly_below_checkout(
    checkout: str, published: list[str], expected: str
) -> None:
    assert support.previous_published_version(checkout, [Version(raw) for raw in published]) == expected


def test_previous_release_refuses_when_none_is_older() -> None:
    with pytest.raises(ValueError, match="No published release"):
        support.previous_published_version("4.0.0b10", [Version("4.0.0b10"), Version("4.0.0")])


def test_explicit_previous_release_allows_the_current_beta_without_a_pypi_lookup() -> None:
    assert support.previous_published_version("4.0.0b10", [], " 4.0.0b10 ") == "4.0.0b10"


@pytest.mark.parametrize("override", ["not-a-version", "https://example.org/sub/secret", "4.0.0+local"])
def test_previous_release_override_requires_an_exact_published_version(override: str) -> None:
    with pytest.raises(ValueError, match="REALVM_UPGRADE_FROM"):
        support.previous_published_version("4.0.0b10", [], override)


def test_upgrade_intent_uses_the_ip_and_default_sni_without_domain_issuance() -> None:
    intent = SetupIntent.model_validate_json(support.build_intent("srv-aaaaaaaaaaaa", IP))

    assert intent.control.public_hostname == ""
    assert intent.access.users == ["default"]
    assert intent.forward_client_ips is False
    assert len(intent.exits) == 1
    for path in intent.exits[0].paths:
        if path.protocol != "reality":
            assert path.tls_sni == IP
    assert next(path for path in intent.exits[0].paths if path.protocol == "reality").reality_sni


def test_upgrade_requests_every_required_app_format() -> None:
    assert {case.user_agent for case in support.SUBSCRIPTION_CASES} == {
        "v2rayNG/1.10.20",
        "Happ/1.0",
        "clash-verge/v2.2",
        "Stash/3.0",
        "SFA/1.12.0",
    }
    assert {case.format for case in support.SUBSCRIPTION_CASES} == {"xray-json", "mihomo", "stash", "singbox"}
    assert len({case.label for case in support.SUBSCRIPTION_CASES}) == 5


def snapshot(body: bytes = b"config\n", header: str = HEADER) -> support.SubscriptionSnapshot:
    return support.SubscriptionSnapshot(body=body, userinfo=header)


def test_identical_subscription_bytes_and_usage_header_pass() -> None:
    captured = {("bob", "mihomo"): snapshot()}
    assert support.compare_subscriptions(captured, captured) == []


@pytest.mark.parametrize("body", [b"config\r\n", b"config \n", b"config\n\n", b"changed\n"])
def test_subscription_comparison_never_normalizes_document_bytes(body: bytes) -> None:
    failures = support.compare_subscriptions({("bob", "mihomo"): snapshot()}, {("bob", "mihomo"): snapshot(body)})

    assert len(failures) == 1
    assert "bob / mihomo" in failures[0]
    assert "document differs" in failures[0]
    assert "byte" in failures[0]


@pytest.mark.parametrize("field", ["upload", "download", "total", "expire"])
def test_every_usage_header_field_including_fixed_expiry_must_survive(field: str) -> None:
    changed = support.parse_userinfo(HEADER)
    changed[field] += 1
    header = "; ".join(f"{key}={value}" for key, value in changed.items())
    failures = support.compare_subscriptions(
        {("bob", "stash"): snapshot()}, {("bob", "stash"): snapshot(header=header)}
    )

    assert len(failures) == 1
    assert "subscription-userinfo differs" in failures[0]
    assert field in failures[0]


def test_missing_or_extra_people_and_formats_cannot_pass_as_an_empty_comparison() -> None:
    before = {("bob", "base64"): snapshot()}
    after = {("default", "base64"): snapshot()}
    assert len(support.compare_subscriptions(before, after)) == 2
    with pytest.raises(ValueError, match="No subscriptions"):
        support.compare_subscriptions({}, {})


@pytest.mark.parametrize(
    "header",
    ["", "upload=1", HEADER + "; token=secret", HEADER.replace("upload=12", "upload=-1"), HEADER + "; upload=12"],
)
def test_malformed_usage_headers_are_not_accepted_as_preserved(header: str) -> None:
    with pytest.raises(ValueError, match="subscription-userinfo"):
        support.parse_userinfo(header)


def test_diff_summaries_never_include_even_encoded_documents() -> None:
    secret = f"https://{IP}/sub/opaque-token"
    before = base64.b64encode(secret.encode())
    after = base64.b64encode((secret + "-changed").encode())
    summary = support.document_diff_summary(before, after)

    assert "first difference" in summary
    assert len(summary) < 180
    assert secret not in summary
    assert before.decode() not in summary
    assert after.decode() not in summary


@pytest.mark.parametrize(
    "raw",
    [
        f"server root@{IP} and 2001:db8::10",
        "Authorization: Bearer opaque-token",
        '"subscription_url": "https://198.51.100.10/sub/opaque-token"',
        "/sub/opaque-token/info and /0123456789abcdef/api",
        "vless://01234567-89ab-cdef-0123-456789abcdef@198.51.100.10:443?path=/secret",
        "HCLOUD_TOKEN=provider-token and provider-token",
        "mrd_0123456789ab_" + "a" * 64,
    ],
    ids=["addresses", "bearer", "subscription-url", "paths", "connection-uri", "provider-token", "api-token"],
)
def test_verifier_redaction_hides_addresses_credentials_and_subscription_paths(raw: str) -> None:
    redacted = support.redact_output(raw, known=("provider-token",))
    for value in (IP, "2001:db8::10", "opaque-token", "0123456789abcdef", "provider-token", "a" * 64):
        assert value not in redacted


def test_redaction_keeps_version_counts_and_plain_status_text() -> None:
    text = "Upgrade 4.0.0b10 -> 4.0.0b11; schema 25; PASS: 10 FAIL: 0 at 12:30:45"
    assert support.redact_output(text) == text


def test_redaction_keeps_file_paths_needed_to_resume_a_kept_fleet() -> None:
    text = "source /tmp/lab/env.sh; read /tmp/lab/home/cluster.yml and control.db"
    assert support.redact_output(text) == text


def test_failure_labels_cannot_smuggle_credentials_into_diff_summaries() -> None:
    label = (f"https://{IP}/sub/opaque-token", "mihomo")
    failures = support.compare_subscriptions({label: snapshot()}, {label: snapshot(b"other")})
    assert IP not in failures[0]
    assert "opaque-token" not in failures[0]


def test_json_readers_require_an_envelope_instead_of_accepting_legacy_payloads() -> None:
    assert support.command_data(json.dumps({"data": {"converged": True}})) == {"converged": True}
    with pytest.raises(ValueError, match="JSON envelope"):
        support.command_data('{"converged": true}')


def test_agent_version_is_read_from_runtime_evidence_not_the_xray_version() -> None:
    data = {
        "nodes": [
            {
                "status": "connected",
                "runtime_available": True,
                "runtime": {"versions": {"agent": "0.1.0-build"}},
                "xray_version": "26.10.15",
            }
        ]
    }
    assert support.fleet_agent_version(data) == "0.1.0-build"
    with pytest.raises(ValueError, match="agent"):
        support.fleet_agent_version({"nodes": [{"status": "connected", "xray_version": "26.10.15"}]})
