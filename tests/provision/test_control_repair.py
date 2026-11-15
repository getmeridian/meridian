"""`meridian check --repair`'s server side: what it refuses, and the forced reinstall it runs."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from meridian.config import UV_VERSION
from meridian.control.models import ServiceConfig
from meridian.control_artifacts import ServicePackage
from meridian.core.errors import MeridianError
from meridian.provision.control_repair import repair, review_repair
from meridian.provision.control_service import INSTALLED_MARKER, render_service_unit
from tests.support.mock_connection import MockConnection

FLEET_ID = "0f1e2d3c-4b5a-4968-8776-655443322110"
OTHER_FLEET_ID = "1f1e2d3c-4b5a-4968-8776-655443322110"
HOST = "198.51.100.10"
RELEASE = ServicePackage(requirement="meridian-vpn==4.0.0")
CONFIG = ServiceConfig(fleet_id=FLEET_ID, admin_token_sha256="a" * 64, listen_port=3001, public_path="/secret")
CONFIG_TEXT = json.dumps(CONFIG.model_dump(mode="json"), indent=2) + "\n"
# Only the repair's own probe names a timeout; the install's readiness wait greps for 401.
PROBE = "--max-time 5"


@pytest.fixture(autouse=True)
def _no_sleep():
    with patch("meridian.provision.control_service.time.sleep"):
        yield


def _server(
    *,
    answers: bool = False,
    config: str = CONFIG_TEXT,
    database: bool = True,
    marker: str = RELEASE.identity,
    lost: str = "",
) -> MockConnection:
    conn = MockConnection()
    if lost:
        conn.when(lost, rc=255)  # The SSH connection dropped while reading it.
    conn.when(PROBE, stdout="401" if answers else "000")
    conn.when("cat /etc/ctld/config.json", stdout=config)
    conn.when("test -s /var/lib/ctld/control.db", rc=0 if database else 1)
    conn.when(f"cat {INSTALLED_MARKER}", stdout=f"{marker}\n" if marker else "")
    return conn


def _refusal(conn: MockConnection, package: ServicePackage = RELEASE) -> MeridianError:
    with pytest.raises(MeridianError) as raised:
        review_repair(conn, package, fleet_id=FLEET_ID, host=HOST)
    return raised.value


def test_a_service_that_answers_is_left_to_apply() -> None:
    conn = _server(answers=True)

    refusal = _refusal(conn)

    assert str(refusal) == "The control service is running; use `meridian apply`."
    assert refusal.category == "user"
    conn.assert_called_with_pattern("http://127.0.0.1:3001/api/health")


@pytest.mark.parametrize(
    "config", ["", "{not json", json.dumps({"fleet_id": FLEET_ID})], ids=["missing", "garbled", "incomplete"]
)
def test_a_server_without_its_configuration_needs_a_restore(config: str) -> None:
    refusal = _refusal(_server(config=config))

    assert "/etc/ctld/config.json" in str(refusal)
    assert refusal.hint == f"Rebuild the control service from a snapshot: meridian restore --to {HOST}"


def test_a_server_without_its_database_needs_a_restore() -> None:
    refusal = _refusal(_server(database=False))

    assert "no database at /var/lib/ctld/control.db" in str(refusal)
    assert f"meridian restore --to {HOST}" in refusal.hint


def test_another_fleets_service_is_never_repaired() -> None:
    other = json.dumps(CONFIG.model_dump(mode="json") | {"fleet_id": OTHER_FLEET_ID})

    refusal = _refusal(_server(config=other))

    assert "another fleet" in str(refusal)


@pytest.mark.parametrize("installed", ["4.0.0b17", "4.0.1"], ids=["older", "newer"])
def test_a_release_repairs_only_its_own_version(installed: str) -> None:
    refusal = _refusal(_server(marker=f"meridian-vpn=={installed}"))

    assert str(refusal) == f"The control server has Meridian {installed} installed, and this is Meridian 4.0.0."
    assert refusal.hint.endswith(f"uvx --from 'meridian-vpn=={installed}' meridian check --repair")
    assert refusal.category == "user"


def test_a_release_without_the_repair_is_pointed_at_a_restart_instead() -> None:
    refusal = _refusal(_server(marker="meridian-vpn==4.0.0b16"))

    assert "Meridian 4.0.0b16 has none" in refusal.hint
    assert "systemctl restart ctld" in refusal.hint
    assert "uvx" not in refusal.hint


def test_a_checkout_repairs_with_its_own_wheel_but_never_over_a_newer_service() -> None:
    wheel = ServicePackage(requirement="", wheel=b"PK\x03\x04", wheel_name="meridian_vpn-4.0.0-py3-none-any.whl")

    review = review_repair(_server(marker="meridian-vpn==3.9.0"), wheel, fleet_id=FLEET_ID, host=HOST)

    assert review.package is wheel
    assert "4.1.0" in str(_refusal(_server(marker="meridian-vpn==4.1.0"), wheel))


@pytest.mark.parametrize(
    "lost", ["cat /etc/ctld/config.json", f"cat {INSTALLED_MARKER}", "test -s"], ids=["config", "record", "database"]
)
def test_unread_evidence_is_unavailable_not_absent(lost: str) -> None:
    refusal = _refusal(_server(lost=lost))

    assert refusal.category == "system" and refusal.retryable


def test_a_server_without_an_install_record_gets_this_version() -> None:
    review = review_repair(_server(marker=""), RELEASE, fleet_id=FLEET_ID, host=HOST)

    assert review.recorded == "" and review.package is RELEASE


def test_a_repair_reinstalls_the_recorded_package_and_keeps_the_configuration() -> None:
    conn = _server()
    conn.when("bin/uv --version", stdout=f"uv {UV_VERSION}\n")
    conn.when("cat /etc/systemd/system/ctld.service", stdout=render_service_unit())
    review = review_repair(conn, RELEASE, fleet_id=FLEET_ID, host=HOST)

    result = repair(conn, review)

    assert result.status == "changed"
    assert review.config == CONFIG
    conn.assert_called_with_pattern("tool install --force --reinstall --python 3.12 meridian-vpn==4.0.0")
    assert "/etc/ctld/config.json" not in conn.write_map
    conn.assert_called_with_pattern("systemctl restart ctld")


def test_a_failed_repair_reports_the_journal() -> None:
    conn = _server()
    conn.when("bin/uv --version", stdout=f"uv {UV_VERSION}\n")
    conn.when("grep -q 401", rc=1)
    conn.when("journalctl", stdout="ModuleNotFoundError: typer\n")
    review = review_repair(conn, RELEASE, fleet_id=FLEET_ID, host=HOST)

    result = repair(conn, review)

    assert result.status == "failed" and "ModuleNotFoundError" in result.detail
