"""The SNI scanner binary is a pinned release, verified before it runs."""

from __future__ import annotations

from unittest.mock import MagicMock

from meridian.commands.scan import scan_for_sni


def _result(stdout: str = "", rc: int = 0) -> MagicMock:
    return MagicMock(stdout=stdout, stderr="", returncode=rc)


def test_scanner_download_is_pinned_and_checksum_verified() -> None:
    calls: list[str] = []

    def run(command: str, timeout: int = 0) -> MagicMock:
        calls.append(command)
        if command == "uname -m":
            return _result("x86_64\n")
        if "sha256sum -c" in command:
            return _result(rc=1)  # tampered binary
        return _result()

    conn = MagicMock()
    conn.run.side_effect = run

    assert scan_for_sni(conn, "198.51.100.1") == []
    download = next(call for call in calls if "curl" in call)
    assert "releases/download/v0.2.3/RealiTLScanner-linux-amd64" in download
    assert "latest" not in download
    assert any("rm -f /tmp/realitlscanner" in call for call in calls)
    assert not any("/tmp/realitlscanner -" in call or "realitlscanner -addr" in call for call in calls)
