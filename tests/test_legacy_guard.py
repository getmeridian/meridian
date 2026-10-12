"""Meridian 4 must never take port 443 from a live 3.x or 2.x deployment."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from meridian.core.errors import InfrastructureEvidenceUnavailableError, ProvisioningError
from meridian.reconciler.resources import ResourceReconcileError
from meridian.setup.legacy_guard import LEGACY_DEPLOYMENT_CHECK, legacy_deployment, refuse_legacy_deployments


def _conn(stdout: str, returncode: int = 0, stderr: str = "") -> MagicMock:
    conn = MagicMock()
    conn.run.return_value = SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    return conn


def test_clean_server_passes() -> None:
    assert legacy_deployment(_conn("meridian-legacy-check-done\n")) == ""


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
    assert expected in legacy_deployment(_conn(f"{marker}\nmeridian-legacy-check-done\n"))


def test_check_never_touches_v4_artifacts() -> None:
    assert "meridian-v4" not in LEGACY_DEPLOYMENT_CHECK
    assert "stream.d/meridian.conf" not in LEGACY_DEPLOYMENT_CHECK  # v4's own control bootstrap writes it


def test_ssh_failure_is_unavailable_not_clean() -> None:
    with pytest.raises(ResourceReconcileError):
        legacy_deployment(_conn("", returncode=255, stderr="ssh: connect to host 198.51.100.10 port 22: timed out"))


def test_truncated_output_is_unavailable_not_clean() -> None:
    with pytest.raises(InfrastructureEvidenceUnavailableError):
        legacy_deployment(_conn(""))


def test_refusal_names_the_server_and_points_to_teardown() -> None:
    clean = _conn("meridian-legacy-check-done\n")
    old = _conn("3x-ui\nmeridian-legacy-check-done\n")

    with pytest.raises(ProvisioningError) as exc_info:
        refuse_legacy_deployments({"srv-a": clean, "srv-b": old}, {"srv-a": "198.51.100.10", "srv-b": "198.51.100.20"})

    assert "198.51.100.20 runs a Meridian 3.x proxy" in str(exc_info.value)
    assert exc_info.value.category == "user"
    assert "meridian teardown 198.51.100.20" in exc_info.value.hint
