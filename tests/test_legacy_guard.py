"""Meridian 4 must never take port 443 from a live 3.x or 2.x deployment."""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from meridian.core.errors import InfrastructureEvidenceUnavailableError, ProvisioningError
from meridian.reconciler.resources import ResourceReconcileError
from meridian.setup.legacy_guard import LEGACY_DEPLOYMENT_CHECK, refuse_legacy_deployments


def _conn(stdout: str, returncode: int = 0, stderr: str = "") -> MagicMock:
    conn = MagicMock()
    conn.run.return_value = SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    return conn


def _refuse(conn: MagicMock) -> None:
    refuse_legacy_deployments({"srv-a": conn}, {"srv-a": "198.51.100.10"})


def test_clean_server_passes() -> None:
    _refuse(_conn("meridian-legacy-check-done\n"))


@pytest.mark.parametrize(
    ("marker", "expected"),
    [
        ("3x-ui", "Meridian 3.x proxy"),
        ("3x-ui-container", "Meridian 3.x proxy"),
        ("3x-relay", "Meridian 3.x relay"),
        ("haproxy", "Meridian 2.x proxy (HAProxy)"),
        ("caddy", "Meridian 2.x proxy (Caddy)"),
    ],
)
def test_each_legacy_marker_is_recognized(marker: str, expected: str) -> None:
    with pytest.raises(ProvisioningError, match=re.escape(expected)):
        _refuse(_conn(f"{marker}\nmeridian-legacy-check-done\n"))


def test_check_never_touches_v4_artifacts() -> None:
    assert "meridian-v4" not in LEGACY_DEPLOYMENT_CHECK
    assert "stream.d/meridian.conf" not in LEGACY_DEPLOYMENT_CHECK  # v4's own control bootstrap writes it


def test_ssh_failure_is_unavailable_not_clean() -> None:
    with pytest.raises(ResourceReconcileError):
        _refuse(_conn("", returncode=255, stderr="ssh: connect to host 198.51.100.10 port 22: timed out"))


def test_truncated_output_is_unavailable_not_clean() -> None:
    with pytest.raises(InfrastructureEvidenceUnavailableError):
        _refuse(_conn(""))


def test_refusal_names_the_server_and_points_to_teardown() -> None:
    clean = _conn("meridian-legacy-check-done\n")
    old = _conn("3x-ui\nmeridian-legacy-check-done\n")

    with pytest.raises(ProvisioningError) as exc_info:
        refuse_legacy_deployments({"srv-a": clean, "srv-b": old}, {"srv-a": "198.51.100.10", "srv-b": "198.51.100.20"})

    assert "198.51.100.20 runs a Meridian 3.x proxy" in str(exc_info.value)
    assert exc_info.value.category == "user"
    assert "meridian teardown 198.51.100.20" in exc_info.value.hint


def test_a_server_without_apt_is_refused_with_its_os_name() -> None:
    centos = _conn("unsupported-os:CentOS Stream 10 (Coughlan)\nmeridian-legacy-check-done\n")

    with pytest.raises(ProvisioningError) as exc_info:
        refuse_legacy_deployments({"srv-a": centos}, {"srv-a": "198.51.100.10"})

    assert "198.51.100.10 runs CentOS Stream 10 (Coughlan)" in str(exc_info.value)
    assert "Ubuntu and Debian only" in str(exc_info.value)
    assert exc_info.value.category == "user"


def test_os_probe_needs_both_apt_get_and_dpkg_query_in_a_real_shell(tmp_path) -> None:
    import os
    import subprocess

    (tmp_path / "apt-get").symlink_to("/bin/echo")  # dpkg-query missing: still unsupported
    result = subprocess.run(
        ["/bin/bash", "-c", LEGACY_DEPLOYMENT_CHECK],
        env={"PATH": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout.splitlines()[0].startswith("unsupported-os:")
    assert result.stdout.splitlines()[-1] == "meridian-legacy-check-done"

    (tmp_path / "dpkg-query").symlink_to("/bin/echo")
    result = subprocess.run(
        ["/bin/bash", "-c", LEGACY_DEPLOYMENT_CHECK],
        env={"PATH": f"{tmp_path}{os.pathsep}/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert "unsupported-os:" not in result.stdout
