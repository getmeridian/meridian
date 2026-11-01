"""`tests/realvm/redact.sed` masks lab secrets and addresses without mangling ordinary output.

The harness itself never runs in CI, but its redaction guards every pasted lab log, so its
cases do; `sed -E` behaves alike in GNU (CI) and BSD (macOS) sed for these patterns.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent / "realvm" / "redact.sed"
TOKEN = "mrd_0123456789ab_" + "a" * 64


def redact(text: str) -> str:
    return subprocess.run(
        ["sed", "-E", "-f", str(SCRIPT)], input=text, capture_output=True, text=True, check=True
    ).stdout.rstrip("\n")


pytestmark = pytest.mark.skipif(shutil.which("sed") is None, reason="needs sed")


@pytest.mark.parametrize(
    ("raw", "masked"),
    [
        ("Connecting to vpn.example.com... then sub.example.com. done", "Connecting to <host>... then <host>. done"),
        (f"token {TOKEN} end", "token <api-token> end"),
        ("addr 2a01:4f8:c0c:1234::1 and fe80::1%eth0", "addr <ip6> and <ip6>%eth0"),
        ("https://198.51.100.1/0123456789abcdef/sub/abc", "https://<ip>/<path>/sub/<token>"),
        ("ips 198.51.100.1 198.51.100.2, at 198.51.100.3.", "ips <ip> <ip>, at <ip>."),
        ("198-51-100-7.sslip.io and 198-51-100-8", "<host> and <ip>"),
        (
            "Subject Alternative Name: DNS:198-51-100-7.sslip.io  critical IP Address:198.51.100.7",
            "Subject Alternative Name: DNS:<host>  critical IP Address:<ip>",
        ),
        (
            "Le_Domain='198-51-100-7.sslip.io' /etc/ssl/meridian/certs/0123456789abcdef/fullchain.pem",
            "Le_Domain='<host>' /etc/ssl/meridian/certs/<path>/fullchain.pem",
        ),
        (
            "hysteria2://u@h:443?obfs-password=S1&fm=%7B%22password%22%3A%22S1%22%7D#Name",
            "hysteria2://u@h:443?obfs-password=<p>&fm=<p>#Name",
        ),
        ('"password": "S2", obfs-password: S3', '"password": "<p>", obfs-password: <p>'),
        ("SECRET.Example.com www.example.xn--p1ai domain.xyz", "<host> <host> <host>"),
    ],
    ids=[
        "sentence-dots",
        "api-token",
        "ipv6",
        "secret-path",
        "adjacent-ips",
        "dashed-ip",
        "certificate-sans",
        "acme-entries",
        "salamander-link",
        "salamander-config",
        "case-and-tlds",
    ],
)
def test_secrets_and_addresses_are_masked(raw: str, masked: str) -> None:
    assert redact(raw) == masked


@pytest.mark.parametrize(
    "kept",
    [
        "/etc/nginx/stream.d/meridian.conf.bak xray.conf.new x.conf",
        "realm-x86_64-unknown-linux-gnu.tar.gz",
        "nginx 1.27.3 build 2026.10.29.1 date 2026-10-29-01",
        "at 12:30:45 on 2026-10-29T23:59:59Z",
        "/blobs/" + "0123456789abcdef" * 4,
    ],
    ids=["config-files", "tarball", "versions-and-dates", "timestamps", "blob-hash"],
)
def test_ordinary_output_is_kept(kept: str) -> None:
    assert redact(kept) == kept
