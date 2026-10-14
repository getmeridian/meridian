"""Pinned, verified RealiTLScanner execution and result parsing."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.commands.scan import ScanUnavailableError, run, scan_for_sni
from meridian.config import REALITL_SCANNER_ASSETS, REALITL_SCANNER_VERSION
from meridian.ssh import CommandResult


class FakeConnection:
    def __init__(self, *, arch: str = "x86_64", scan_returncode: int = 0, cidr: str = "198.51.100.8/24") -> None:
        self.arch = arch
        self.cidr = cidr
        self.scan_returncode = scan_returncode
        self.commands: list[str] = []
        # RealiTLScanner 0.2.3 output: certificate details precede CERT_DOMAIN.
        self.csv_output = (
            "IP,ORIGIN,TLS,ALPN,CURVE,CERT_LENGTH,CERT_SIGNATURE,CERT_PUBLICKEY,CERT_DOMAIN,CERT_ISSUER,GEO_CODE\n"
            '203.0.113.8,"Example, Inc",TLS 1.3,h2,X25519,2048,sig,rsa,cdn.test,issuer,GB\n'
            "203.0.113.9,origin,TLS 1.3,h2,X25519,2048,sig,rsa,cdn.test,issuer,GB\n"
            "203.0.113.10,origin,TLS 1.3,h2,X25519,2048,sig,rsa,*.wild.test,issuer,GB\n"
            "203.0.113.11,origin,TLS 1.3,h2,X25519,2048,sig,rsa,bad host,issuer,GB\n"
        )

    def run(self, command: str, **_kwargs: object) -> CommandResult:
        self.commands.append(command)
        if command == "uname -m":
            return CommandResult(args=command, returncode=0, stdout=f"{self.arch}\n")
        if command.startswith("ip addr show"):
            return CommandResult(args=command, returncode=0, stdout=f"{self.cidr}\n")
        return CommandResult(args=command, returncode=self.scan_returncode, stdout=self.csv_output)


def test_scanner_is_pinned_checksum_verified_and_isolated() -> None:
    connection = FakeConnection()

    with patch("rich.status.Status"):
        domains = scan_for_sni(connection, "198.51.100.8")  # type: ignore[arg-type]

    assert domains == ["cdn.test"]
    command = connection.commands[-1]
    asset_name, digest = REALITL_SCANNER_ASSETS["x86_64"]
    assert f"/v{REALITL_SCANNER_VERSION}/{asset_name}" in command
    assert digest in command
    assert "sha256sum -c -" in command
    assert "mktemp -d /tmp/meridian-scan.XXXXXX" in command
    assert "trap 'rm -rf \"$tmpdir\"' EXIT" in command
    assert "/releases/latest/" not in command


def test_scanner_selects_published_arm64_asset() -> None:
    connection = FakeConnection(arch="aarch64")

    with patch("rich.status.Status"):
        scan_for_sni(connection, "198.51.100.8")  # type: ignore[arg-type]

    asset_name, digest = REALITL_SCANNER_ASSETS["aarch64"]
    assert asset_name in connection.commands[-1]
    assert digest in connection.commands[-1]


def test_scanner_does_not_accept_results_after_verification_failure() -> None:
    connection = FakeConnection(scan_returncode=1)

    with patch("rich.status.Status"):
        domains = scan_for_sni(connection, "198.51.100.8")  # type: ignore[arg-type]

    assert domains == []


def test_strict_scanner_distinguishes_operational_failure_from_empty_results() -> None:
    connection = FakeConnection(scan_returncode=1)

    with patch("rich.status.Status"), pytest.raises(ScanUnavailableError):
        scan_for_sni(connection, "198.51.100.8", strict=True)  # type: ignore[arg-type]


def _resolved(connection: FakeConnection) -> SimpleNamespace:
    return SimpleNamespace(ip="198.51.100.8", user="root", conn=connection)


def test_standalone_scan_returns_findings_exit_for_completed_empty_scan() -> None:
    connection = FakeConnection()
    resolved = _resolved(connection)

    with (
        patch("meridian.commands.scan.resolve_server", return_value=resolved),
        patch("meridian.commands.scan.ensure_server_connection", return_value=resolved),
        patch("meridian.commands.scan.scan_for_sni", return_value=[]),
        pytest.raises(typer.Exit) as exc_info,
    ):
        run(ip="198.51.100.8")

    assert exc_info.value.exit_code == 4


def test_standalone_scan_returns_unavailable_exit_for_scanner_failure() -> None:
    connection = FakeConnection()
    resolved = _resolved(connection)

    with (
        patch("meridian.commands.scan.resolve_server", return_value=resolved),
        patch("meridian.commands.scan.ensure_server_connection", return_value=resolved),
        patch(
            "meridian.commands.scan.scan_for_sni",
            side_effect=ScanUnavailableError("scanner failed"),
        ),
        pytest.raises(typer.Exit) as exc_info,
    ):
        run(ip="198.51.100.8")

    assert exc_info.value.exit_code == 3


def test_v4_scan_selection_does_not_mutate_reviewed_intent() -> None:
    connection = FakeConnection()
    resolved = _resolved(connection)
    cluster = MagicMock()
    intent = object()
    cluster.topology_intent = intent

    with (
        patch("meridian.commands.scan.resolve_server", return_value=resolved),
        patch("meridian.commands.scan.ensure_server_connection", return_value=resolved),
        patch("meridian.commands.scan.scan_for_sni", return_value=["cdn.test"]),
        patch("meridian.commands.scan.prompt", return_value="1"),
        patch("meridian.commands._helpers.ClusterConfig.load", return_value=cluster),
        patch("meridian.commands.scan.ok") as rendered_ok,
        patch("meridian.commands.scan.warn") as rendered_warn,
    ):
        run(ip="198.51.100.8")

    assert cluster.topology_intent is intent
    cluster.save.assert_not_called()
    rendered_ok.assert_called_once_with("Selected: cdn.test")
    assert any("review-managed" in str(call) for call in rendered_warn.call_args_list)


def test_standalone_scan_rejects_non_numeric_selection() -> None:
    connection = FakeConnection()
    resolved = _resolved(connection)

    with (
        patch("meridian.commands.scan.resolve_server", return_value=resolved),
        patch("meridian.commands.scan.ensure_server_connection", return_value=resolved),
        patch("meridian.commands.scan.scan_for_sni", return_value=["cdn.test"]),
        patch("meridian.commands.scan.prompt", return_value="first"),
        patch("meridian.commands._helpers.ClusterConfig.load") as load_cluster,
        pytest.raises(typer.Exit) as exc_info,
    ):
        run(ip="198.51.100.8")

    assert exc_info.value.exit_code == 2
    load_cluster.assert_not_called()


def test_scanner_reads_the_domain_column_by_name_in_any_release_format() -> None:
    connection = FakeConnection()
    connection.csv_output = "IP,ORIGIN,CERT_DOMAIN,CERT_ISSUER,GEO_CODE\n203.0.113.8,origin,cdn.test,issuer,GB\n"

    assert scan_for_sni(connection, "198.51.100.8") == ["cdn.test"]


def test_strict_scanner_reports_an_unknown_output_format_as_unavailable() -> None:
    connection = FakeConnection()
    connection.csv_output = "ADDR,NAME\n203.0.113.8,cdn.test\n"

    with pytest.raises(ScanUnavailableError):
        scan_for_sni(connection, "198.51.100.8", strict=True)


def test_neighbor_targets_need_dns_inside_the_subnet_and_reality_tls() -> None:
    from meridian.diagnostics.camouflage import neighbor_camouflage_targets

    connection = FakeConnection()
    connection.csv_output = (
        "IP,CERT_DOMAIN\n198.51.100.20,shop.near.test\n198.51.100.21,cdn.far.test\n198.51.100.22,old.near.test\n"
    )
    answers = {
        "getent ahostsv4 shop.near.test": "198.51.100.20   STREAM shop.near.test\n",
        # A CDN-fronted name resolves elsewhere: its SNI would not match the server's network.
        "getent ahostsv4 cdn.far.test": "203.0.113.50    STREAM cdn.far.test\n",
        "getent ahostsv4 old.near.test": "198.51.100.22   STREAM old.near.test\n",
        "https://shop.near.test/": "2",
        "https://old.near.test/": "1.1",  # no HTTP/2, which Reality needs
    }
    scan_run = connection.run

    def run(command: str, **kwargs: object) -> CommandResult:
        for needle, stdout in answers.items():
            if needle in command:
                return CommandResult(args=command, returncode=0, stdout=stdout)
        return scan_run(command, **kwargs)

    connection.run = run  # type: ignore[method-assign]

    assert neighbor_camouflage_targets(connection, "198.51.100.8") == ["shop.near.test"]  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("cidr", "network"),
    [
        ("198.51.100.8/32", "198.51.100.0/24"),
        ("198.51.100.8/29", "198.51.100.0/24"),
        ("198.51.100.8/22", "198.51.100.0/22"),
    ],
    ids=["cloud-point-to-point", "small-block", "wider-subnet-kept"],
)
def test_scan_covers_at_least_the_servers_slash_24(cidr: str, network: str) -> None:
    from meridian.diagnostics.camouflage import scan_neighbors

    connection = FakeConnection(cidr=cidr)

    assert str(scan_neighbors(connection, "198.51.100.8").network) == network  # type: ignore[arg-type]
