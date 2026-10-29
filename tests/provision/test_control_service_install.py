"""Control-service install step: fresh, converged, dev-wheel, and failure paths."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from meridian.config import UV_SHA256, UV_VERSION
from meridian.control.models import ServiceConfig
from meridian.control_artifacts import ServicePackage
from meridian.provision.control_service import (
    INSTALLED_MARKER,
    ControlServiceInstall,
    InstallControlService,
    render_service_unit,
)
from meridian.provision.steps import ProvisionContext
from tests.support.mock_connection import MockConnection

FLEET_ID = "0f1e2d3c-4b5a-4968-8776-655443322110"
PYPI = ServicePackage(requirement="meridian-vpn==4.0.0")


def _install(package: ServicePackage = PYPI) -> InstallControlService:
    config = ServiceConfig(fleet_id=FLEET_ID, admin_token_sha256="a" * 64)
    return InstallControlService(ControlServiceInstall(package=package, config=config))


def _ctx() -> ProvisionContext:
    return ProvisionContext(ip="198.51.100.10", user="root", domain="", sni="", xhttp_enabled=False)


@pytest.fixture(autouse=True)
def _no_sleep():
    with patch("meridian.provision.control_service.time.sleep"):
        yield


def test_fresh_install_verifies_uv_and_installs_the_pinned_package() -> None:
    conn = MockConnection()
    conn.when("bin/uv --version", rc=127)
    conn.when("uname -m", stdout="x86_64\n")
    conn.when(f"cat {INSTALLED_MARKER}", stdout="")

    result = _install().run(conn, _ctx())

    assert result.status == "changed"
    conn.assert_called_with_pattern(f"{UV_SHA256['x86_64']}")
    conn.assert_called_with_pattern("tool install --force --python 3.12 meridian-vpn==4.0.0")
    assert conn.write_map[INSTALLED_MARKER].decode().strip() == PYPI.identity


def test_converged_install_changes_and_restarts_nothing() -> None:
    install = _install()
    conn = MockConnection()
    conn.when("bin/uv --version", stdout=f"uv {UV_VERSION} (x86_64-unknown-linux-musl)\n")
    conn.when(f"cat {INSTALLED_MARKER}", stdout=f"{PYPI.identity}\n")
    conn.when("systemctl is-active", stdout="active\n")
    config = json.dumps(install.install.config.model_dump(mode="json"), indent=2) + "\n"
    conn.when("cat /etc/ctld/config.json", stdout=config)
    conn.when("cat /etc/systemd/system/ctld.service", stdout=render_service_unit())

    result = install.run(conn, _ctx())

    assert result.status == "ok"
    conn.assert_not_called_with_pattern("uv.tgz")
    conn.assert_not_called_with_pattern("tool install")
    conn.assert_not_called_with_pattern("systemctl restart")


def test_dev_build_uploads_the_wheel_and_installs_that_file() -> None:
    wheel = ServicePackage(requirement="", wheel=b"PK\x03\x04wheel", wheel_name="meridian_vpn-4.0.0-py3-none-any.whl")
    conn = MockConnection()
    conn.when("bin/uv --version", stdout=f"uv {UV_VERSION}\n")

    _install(wheel).run(conn, _ctx())

    path = "/opt/ctld/wheels/meridian_vpn-4.0.0-py3-none-any.whl"
    assert conn.write_map[path] == wheel.wheel
    conn.assert_called_with_pattern(f"tool install --force --python 3.12 {path}")


def test_unknown_architecture_fails_before_downloading() -> None:
    conn = MockConnection()
    conn.when("bin/uv --version", rc=127)
    conn.when("uname -m", stdout="riscv64\n")

    result = _install().run(conn, _ctx())

    assert result.status == "failed"
    assert "riscv64" in result.detail
    conn.assert_not_called_with_pattern("curl")


def test_service_that_never_answers_reports_its_journal() -> None:
    conn = MockConnection()
    conn.when("bin/uv --version", stdout=f"uv {UV_VERSION}\n")
    conn.when(f"cat {INSTALLED_MARKER}", stdout=f"{PYPI.identity}\n")
    conn.when("/api/health", rc=1)
    conn.when("journalctl", stdout="Traceback: boom\n")

    result = _install().run(conn, _ctx())

    assert result.status == "failed"
    assert "boom" in result.detail


def test_service_unit_runs_unprivileged_with_only_its_state_writable() -> None:
    unit = render_service_unit()

    assert "User=ctld" in unit
    assert "ProtectSystem=strict" in unit
    assert "ReadWritePaths=/var/lib/ctld" in unit


@pytest.mark.parametrize(
    ("marker", "refused"),
    [
        ("meridian-vpn==3.9.0", False),
        ("meridian_vpn-4.0.0b8-py3-none-any.whl:" + "d" * 64, False),
        ("meridian-vpn==4.0.1", True),
        ("meridian_vpn-4.1.0-py3-none-any.whl:" + "d" * 64, True),
        ("something else", False),
    ],
    ids=["older-release", "older-wheel", "newer-release", "newer-wheel", "unrecognised"],
)
def test_an_install_never_downgrades_a_newer_service(marker: str, refused: bool) -> None:
    conn = MockConnection()
    conn.when("bin/uv --version", stdout=f"uv {UV_VERSION}\n")
    conn.when(f"cat {INSTALLED_MARKER}", stdout=f"{marker}\n")

    result = _install().run(conn, _ctx())

    if refused:
        assert result.status == "failed"
        assert "newer than this Meridian (4.0.0)" in result.detail
        conn.assert_not_called_with_pattern("tool install")
    else:
        conn.assert_called_with_pattern("tool install --force")


@pytest.mark.parametrize("rc", [124, 255, 1], ids=["timed-out", "connection-lost", "unreadable"])
def test_an_unread_install_record_never_forces_an_install(rc: int) -> None:
    """It may name a newer service, which `uv tool install --force` would downgrade."""
    conn = MockConnection()
    conn.when("bin/uv --version", stdout=f"uv {UV_VERSION}\n")
    conn.when(f"cat {INSTALLED_MARKER}", rc=rc, stderr="cat: Permission denied" if rc == 1 else "")

    result = _install().run(conn, _ctx())

    assert result.status == "failed" and "could not read which service package" in result.detail
    conn.assert_not_called_with_pattern("tool install")
