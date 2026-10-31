"""Server-side retirement removes only Meridian-tagged artifacts on the recorded server."""

from __future__ import annotations

import shlex
import subprocess
from types import SimpleNamespace

import pytest

from meridian.cluster import ManagedResourceBinding
from meridian.compiler.models import (
    CertificatePayload,
    ControlServicePayload,
)
from meridian.host_runtime import certificate_directory
from meridian.reconciler.resources import ResourceReconcileError
from meridian.reconciler.server_render import (
    legacy_realm_config_path,
    legacy_realm_service_name,
    realm_config_path,
    realm_service_name,
)
from meridian.reconciler.server_retirement import (
    CertificateRetirement,
    FirewallRetirement,
    NginxArtifactRetirement,
    RealmHopRetirement,
)
from tests.support.mock_connection import MockConnection

_MARKER = "fw-0123456789abcdef"
_STREAM = "/etc/nginx/stream.d/meridian-v4-0123456789abcdef.conf"
_HTTP = "/etc/nginx/sites-available/meridian-v4-fedcba9876543210.conf"


def _driver(cls, conn: MockConnection):
    driver = cls()
    driver.context = SimpleNamespace(connection=lambda server_ref: conn)
    return driver


def _binding(remote_id: str, server_ref: str = "srv-a") -> ManagedResourceBinding:
    return ManagedResourceBinding(logical_id="x", resource_kind="k", remote_id=remote_id, server_ref=server_ref)


def test_firewall_rules_are_deleted_by_marker_highest_first() -> None:
    conn = MockConnection()

    _driver(FirewallRetirement, conn).retire(_binding(_MARKER))

    command, legacy = conn.calls
    assert f"# {_MARKER}$" in command and "sort -rn" in command and "ufw --force delete" in command
    # The binding's logical ID ("x") also owns a rule under its pre-rename comment.
    assert "# meridian-v4-" in legacy and "ufw --force delete" in legacy


def test_legacy_firewall_binding_is_deleted_once() -> None:
    from meridian.reconciler.server_render import legacy_firewall_marker

    conn = MockConnection()

    _driver(FirewallRetirement, conn).retire(_binding(legacy_firewall_marker("x")))

    assert len(conn.calls) == 1


def test_http_artifact_removes_file_and_enabled_link_then_reloads() -> None:
    conn = MockConnection()

    _driver(NginxArtifactRetirement, conn).retire(_binding(_HTTP))

    (command,) = conn.calls
    assert _HTTP in command and _HTTP.replace("sites-available", "sites-enabled") in command
    assert command.index("nginx -t") < command.index("reload-or-restart nginx")


def test_failed_nginx_reload_is_reported() -> None:
    conn = MockConnection()
    conn.when("rm -f", rc=1, stdout="nginx: [emerg] bad config")

    with pytest.raises(ResourceReconcileError, match="did not reload"):
        _driver(NginxArtifactRetirement, conn).retire(_binding(_STREAM))


@pytest.mark.parametrize(
    ("cls", "remote_id"),
    [(FirewallRetirement, "meridian-v4-x; rm -rf /"), (NginxArtifactRetirement, "/etc/passwd")],
)
def test_unexpected_ids_are_refused_without_touching_the_server(cls, remote_id: str) -> None:
    conn = MockConnection()

    with pytest.raises(ResourceReconcileError, match="Unexpected"):
        _driver(cls, conn).retire(_binding(remote_id))
    assert conn.calls == []


def test_bindings_without_a_recorded_server_are_refused() -> None:
    with pytest.raises(ResourceReconcileError, match="before Meridian tracked its server"):
        _driver(FirewallRetirement, MockConnection()).retire(_binding(_MARKER, server_ref=""))


def test_realm_hop_unit_and_config_are_removed_and_verified_stopped() -> None:
    conn = MockConnection()
    service = realm_service_name("realm:relay-chain:0")

    _driver(RealmHopRetirement, conn).retire(
        ManagedResourceBinding(logical_id="realm:relay-chain:0", remote_id=service, server_ref="srv-relay")
    )

    (command,) = conn.calls
    assert command.index('systemctl disable --now "$unit"') < command.index("rm -f")
    assert f"/etc/systemd/system/{service}.service" in command
    assert realm_config_path("realm:relay-chain:0") in command
    assert "daemon-reload" in command and f"! systemctl is-active --quiet {service}" in command


def test_realm_hop_stops_even_when_the_legacy_unit_never_existed(tmp_path) -> None:
    """A multi-unit `systemctl disable --now` aborts on a missing unit; a real relay kept forwarding."""
    conn = MockConnection()
    service = realm_service_name("realm:relay-chain:0")
    _driver(RealmHopRetirement, conn).retire(
        ManagedResourceBinding(logical_id="realm:relay-chain:0", remote_id=service, server_ref="srv-relay")
    )
    (command,) = conn.calls
    state = tmp_path / "active"
    state.write_text(service)
    fake = tmp_path / "systemctl"
    fake.write_text(
        "#!/bin/sh\n"
        f'state="{state}"\n'
        'case "$1" in\n'
        '  disable|stop) shift; [ "$1" = --now ] && shift\n'
        '    for unit in "$@"; do [ "$unit" = "$(cat "$state")" ] || exit 5; done\n'
        '    [ "$1" = "$(cat "$state")" ] && : > "$state"; exit 0;;\n'
        '  is-active) [ "$3" = "$(cat "$state")" ] && [ -n "$3" ];;\n'
        "  *) exit 0;;\n"
        "esac\n"
    )
    fake.chmod(0o755)
    runnable = command.replace("rm -f", "true")
    result = subprocess.run(["sh", "-c", runnable], env={"PATH": f"{tmp_path}:/usr/bin:/bin"}, check=False)
    assert result.returncode == 0
    assert state.read_text() == ""


def test_pre_rename_realm_hop_binding_removes_both_unit_names() -> None:
    conn = MockConnection()
    legacy = legacy_realm_service_name("realm:relay-chain:0")

    _driver(RealmHopRetirement, conn).retire(
        ManagedResourceBinding(logical_id="realm:relay-chain:0", remote_id=legacy, server_ref="srv-relay")
    )

    (command,) = conn.calls
    assert f"/etc/systemd/system/{legacy}.service" in command
    assert legacy_realm_config_path("realm:relay-chain:0") in command
    assert command.endswith(f"! systemctl is-active --quiet {legacy}")


def test_realm_hop_still_running_after_removal_is_a_failure() -> None:
    conn = MockConnection()
    conn.when("systemctl disable", rc=1)
    service = realm_service_name("realm:relay-chain:1")

    with pytest.raises(ResourceReconcileError, match="still running"):
        _driver(RealmHopRetirement, conn).retire(
            ManagedResourceBinding(logical_id="realm:relay-chain:1", remote_id=service, server_ref="srv-relay")
        )


def test_realm_hop_refuses_a_service_name_that_does_not_match_its_resource() -> None:
    conn = MockConnection()

    with pytest.raises(ResourceReconcileError, match="Unexpected Realm service"):
        _driver(RealmHopRetirement, conn).retire(
            ManagedResourceBinding(logical_id="realm:relay-chain:0", remote_id="sshd", server_ref="srv-relay")
        )
    assert conn.calls == []


def _certificate_driver(conn: MockConnection, *payloads: object) -> CertificateRetirement:
    driver = CertificateRetirement()
    resources = [SimpleNamespace(payload=payload) for payload in payloads]
    driver.context = SimpleNamespace(connection=lambda server_ref: conn, plan=SimpleNamespace(resources=resources))
    return driver


_DOMAIN_CERT = f"{certificate_directory('vpn.example.com')}/fullchain.pem"


def test_removed_exit_certificate_stops_renewing_and_drops_the_challenge_listener() -> None:
    conn = MockConnection()

    _certificate_driver(conn).retire(_binding(_DOMAIN_CERT, server_ref="srv-exit-b"))

    (command,) = conn.calls
    # The acme.sh entry is found by its recorded install path, so renewal stops.
    record = shlex.quote(f"Le_RealFullChainPath='{_DOMAIN_CERT}'")
    assert f"grep -l -F {record} /root/.acme.sh/*/*.conf" in command
    assert 'rm -rf "$(dirname "$conf")"' in command
    assert f"rm -f {_DOMAIN_CERT} {_DOMAIN_CERT.replace('fullchain', 'key')}" in command
    challenge = "/etc/nginx/sites-available/meridian-v4-acme.conf /etc/nginx/sites-enabled/meridian-v4-acme.conf"
    assert f"rm -f {challenge}" in command
    assert "nginx -t" in command


def test_certificate_on_a_server_that_keeps_another_keeps_the_challenge_listener() -> None:
    conn = MockConnection()
    other = CertificatePayload(server_ref="srv-exit-b", hostname="other.example.com")

    _certificate_driver(conn, other).retire(_binding(_DOMAIN_CERT, server_ref="srv-exit-b"))

    (command,) = conn.calls
    assert "Le_RealFullChainPath" in command
    assert "meridian-v4-acme.conf" not in command


def test_the_control_servers_shared_ip_certificate_is_never_removed() -> None:
    conn = MockConnection()
    runtime = ControlServicePayload.model_construct(server_ref="srv-a")

    _certificate_driver(conn, runtime).retire(_binding("/etc/ssl/meridian/fullchain.pem"))

    assert conn.calls == []


def test_a_panel_with_its_own_domain_lets_the_unused_ip_certificate_go() -> None:
    conn = MockConnection()
    runtime = ControlServicePayload.model_construct(server_ref="srv-a", public_hostname="panel.example.com")

    _certificate_driver(conn, runtime).retire(_binding("/etc/ssl/meridian/fullchain.pem"))

    (command,) = conn.calls
    assert shlex.quote("Le_RealFullChainPath='/etc/ssl/meridian/fullchain.pem'") in command
    # The panel still needs the ACME challenge listener for its own certificate.
    assert "meridian-v4-acme.conf" not in command


def test_a_panel_domains_certificate_is_never_removed() -> None:
    conn = MockConnection()
    runtime = ControlServicePayload.model_construct(server_ref="srv-a", public_hostname="vpn.example.com")

    _certificate_driver(conn, runtime).retire(_binding(_DOMAIN_CERT))

    assert conn.calls == []


def test_a_certificate_still_planned_under_the_same_files_is_only_forgotten() -> None:
    conn = MockConnection()
    same = CertificatePayload(server_ref="srv-exit-b", hostname="vpn.example.com")

    _certificate_driver(conn, same).retire(_binding(_DOMAIN_CERT, server_ref="srv-exit-b"))

    assert conn.calls == []


def test_certificate_retirement_refuses_unexpected_paths() -> None:
    conn = MockConnection()

    with pytest.raises(ResourceReconcileError, match="Unexpected certificate path"):
        _certificate_driver(conn).retire(_binding("/etc/passwd"))
    assert conn.calls == []


def test_certificate_retirement_deletes_only_the_matching_acme_entry(tmp_path) -> None:
    """Run the real command against a scratch acme.sh home."""
    acme = tmp_path / "acme"
    for name, path in (("vpn.example.com_ecc", _DOMAIN_CERT), ("other.example.com_ecc", "/elsewhere/fullchain.pem")):
        (acme / name).mkdir(parents=True)
        (acme / name / f"{name}.conf").write_text(f"Le_Domain='{name}'\nLe_RealFullChainPath='{path}'\n")
    conn = MockConnection()
    other = CertificatePayload(server_ref="srv-exit-b", hostname="other.example.com")
    _certificate_driver(conn, other).retire(_binding(_DOMAIN_CERT, server_ref="srv-exit-b"))
    # Only the acme.sh home moves; removing the absent certificate files is a no-op.
    command = conn.calls[0].replace("/root/.acme.sh", str(acme))

    subprocess.run(["bash", "-c", command], check=True)

    assert sorted(path.name for path in acme.iterdir()) == ["other.example.com_ecc"]
