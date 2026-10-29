"""Device keys, snapshot pulls, the one hook after every change, and the recovery key's first showing."""

from __future__ import annotations

import stat
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
import yaml

from meridian import config
from meridian.backup import BackupError
from meridian.backup.keys import create_device_key, device_key_path, load_device_key, snapshots_dir
from meridian.backup.pull import KEEP, pull_snapshot, saved_snapshots
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.commands import backup as backup_commands
from meridian.control.client import ControlClient
from meridian.control.restore import read_deployer_state
from meridian.control.snapshot import decrypt_snapshot
from tests.support.control_service import ADMIN_TOKEN, FLEET_ID, serve_control
from tests.support.topology import single_exit_intent


@pytest.fixture
def fleet(tmp_home: Path, tmp_path: Path) -> Iterator[ClusterConfig]:
    """A saved control-service fleet whose service runs on loopback."""
    with serve_control(tmp_path / "state") as url:
        cluster = ClusterConfig(
            panel=PanelConfig(url=url, api_token=ADMIN_TOKEN, server_ip="198.51.100.1"),
            topology_intent=single_exit_intent(control="srv-control", users=["alice"]).model_copy(
                update={"control": single_exit_intent().control.model_copy(update={"backend": "meridian"})}
            ),
            fleet_id=FLEET_ID,
            active_generation=3,
        )
        cluster.save()
        config.SERVER_PROFILES_FILE.write_text("[]\n", encoding="utf-8")
        yield cluster


def test_the_first_pull_makes_and_registers_this_devices_key(fleet: ClusterConfig) -> None:
    pulled = pull_snapshot(fleet, name="laptop")

    key = load_device_key()
    assert key is not None and pulled.registered
    assert stat.S_IMODE(device_key_path().stat().st_mode) == 0o600
    assert stat.S_IMODE(pulled.path.stat().st_mode) == 0o600
    assert pulled.path.parent == snapshots_dir()
    assert decrypt_snapshot(pulled.path.read_bytes(), [key.secret]).header.fleet_id == FLEET_ID
    with ControlClient(fleet.panel.url, ADMIN_TOKEN) as client:
        assert [(device.name, device.public_key) for device in client.device_keys()] == [("laptop", key.public)]
    assert not pull_snapshot(fleet).registered


def test_the_snapshot_carries_this_machines_fleet_files_without_the_admin_token(fleet: ClusterConfig) -> None:
    pulled = pull_snapshot(fleet)
    key = load_device_key()
    assert key is not None

    deposited = read_deployer_state(decrypt_snapshot(pulled.path.read_bytes(), [key.secret]).database)

    assert deposited is not None and deposited.generation == 3
    assert ADMIN_TOKEN not in deposited.cluster_yml
    assert yaml.safe_load(deposited.cluster_yml)["fleet_id"] == FLEET_ID
    assert deposited.servers_json == "[]\n"


def test_only_the_newest_snapshots_are_kept(fleet: ClusterConfig) -> None:
    create_device_key()
    snapshots_dir().mkdir(parents=True)
    for index in range(KEEP + 2):
        (snapshots_dir() / f"20200101-00000{index}-e1-r0.snapshot").write_bytes(b"old")

    pulled = pull_snapshot(fleet)

    kept = saved_snapshots()
    assert len(kept) == KEEP and kept[0] == pulled.path


def test_a_revoked_device_key_is_never_registered_again_silently(fleet: ClusterConfig) -> None:
    key = create_device_key()
    with ControlClient(fleet.panel.url, ADMIN_TOKEN) as client:
        client.revoke_device_key(client.register_device_key("laptop", key.public).id)

    with pytest.raises(BackupError, match="revoked"):
        pull_snapshot(fleet)


def test_a_malformed_device_key_is_an_error_never_replaced(fleet: ClusterConfig) -> None:
    device_key_path().write_text("garbage\n", encoding="utf-8")

    with pytest.raises(BackupError, match="does not hold a device key"):
        pull_snapshot(fleet)
    assert device_key_path().read_text(encoding="utf-8") == "garbage\n"


# -- the hook -------------------------------------------------------------------------------


def _hook() -> Callable[[], None]:
    closers: list[Callable[[], None]] = []
    backup_commands.pull_after_mutations(closers.append)
    (close,) = closers
    return close


def test_a_write_to_the_fleets_service_pulls_once_when_the_command_closes(fleet: ClusterConfig) -> None:
    close = _hook()
    with ControlClient(fleet.panel.url, ADMIN_TOKEN) as client:
        client.put_user("bob")
        client.put_user("carol")
        assert saved_snapshots() == []

    close()

    assert len(saved_snapshots()) == 1


def test_reads_alone_never_pull(fleet: ClusterConfig) -> None:
    close = _hook()
    with ControlClient(fleet.panel.url, ADMIN_TOKEN) as client:
        client.list_users()

    close()

    assert saved_snapshots() == []


def test_a_failed_pull_warns_and_never_raises(fleet: ClusterConfig, capsys: pytest.CaptureFixture[str]) -> None:
    device_key_path().write_text("garbage\n", encoding="utf-8")
    close = _hook()
    with ControlClient(fleet.panel.url, ADMIN_TOKEN) as client:
        client.put_user("bob")

    close()

    assert "Could not back up the control service's state" in capsys.readouterr().err
    assert saved_snapshots() == []


def test_a_write_to_another_service_does_not_pull_this_fleet(fleet: ClusterConfig, tmp_path: Path) -> None:
    close = _hook()
    with serve_control(tmp_path / "other") as other, ControlClient(other, ADMIN_TOKEN) as client:
        client.put_user("bob")

    close()

    assert saved_snapshots() == []


def test_the_hook_says_when_no_recovery_key_exists(fleet: ClusterConfig, capsys: pytest.CaptureFixture[str]) -> None:
    close = _hook()
    with ControlClient(fleet.panel.url, ADMIN_TOKEN) as client:
        client.put_user("bob")

    close()

    assert "No recovery key yet" in capsys.readouterr().err


def test_deploy_shows_a_new_recovery_key_once(fleet: ClusterConfig, capsys: pytest.CaptureFixture[str]) -> None:
    backup_commands.offer_recovery_key(fleet)
    first = capsys.readouterr().err
    backup_commands.offer_recovery_key(fleet)

    assert "AGE-SECRET-KEY-1" in first and "shown once" in first
    assert "AGE-SECRET-KEY-1" not in capsys.readouterr().err
    with ControlClient(fleet.panel.url, ADMIN_TOKEN) as client:
        assert client.recovery_key().public_key
    assert "AGE-SECRET-KEY" not in config.CLUSTER_CONFIG.read_text(encoding="utf-8")


def test_an_unfinished_restore_stays_on_this_machine(fleet: ClusterConfig) -> None:
    """Carried into a fresh context, it would hold that context to a target that may be gone."""
    from meridian.backup.pull import deployer_state
    from meridian.cluster import ControlRestore

    fleet.control_restore = ControlRestore(step="loaded", to_server_ref="srv-gone", fleet_epoch=2)
    assert "control_restore" not in yaml.safe_load(deployer_state(fleet).cluster_yml)

    fleet.control_restore = ControlRestore(step="done", to_server_ref="srv-new", fleet_epoch=2)
    assert yaml.safe_load(deployer_state(fleet).cluster_yml)["control_restore"]["fleet_epoch"] == 2


def test_the_deposit_keeps_no_token_or_panel_leftover(fleet: ClusterConfig) -> None:
    """It sits in control.db as it is; a restore mints new tokens and needs none of a Remnawave panel's."""
    from meridian.backup.pull import deployer_state
    from meridian.cluster import ControlMigration, TelegramConfig

    fleet.panel.admin_pass = "panel-password-1"
    fleet.telegram = TelegramConfig(bot_token="123456:bot-token-1")
    fleet.control_migration = ControlMigration(step="done", service_token="service-token-1")

    deposited = deployer_state(fleet).cluster_yml

    for secret in ("panel-password-1", "bot-token-1", "service-token-1", fleet.panel.api_token):
        assert secret not in deposited
    assert fleet.control_migration.service_token == "service-token-1"  # this machine's copy is untouched


def test_a_key_another_run_made_first_is_kept_and_used(fleet: ClusterConfig) -> None:
    """Two first pulls at once: the second must not replace the key the first sealed its snapshot to."""
    first = create_device_key()
    before = device_key_path().read_bytes()

    second = create_device_key()

    assert second == first and device_key_path().read_bytes() == before
    assert [path.name for path in device_key_path().parent.iterdir() if path.name.endswith(".tmp")] == []


def _failing(error: Exception | None = None, status: int = 200) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        if error is not None:
            raise error
        return httpx.Response(status, json={"error": {"message": "boom"}})

    return httpx.MockTransport(handle)


@pytest.mark.parametrize(
    ("transport", "reported"),
    [
        (_failing(httpx.ReadTimeout("lost")), True),
        (_failing(httpx.RemoteProtocolError("closed")), True),
        (_failing(status=504), True),
        (_failing(status=500), True),
        (_failing(httpx.ConnectError("refused")), False),
        (_failing(status=409), False),
    ],
    ids=["reply-timed-out", "connection-dropped", "gateway-timeout", "server-error", "never-connected", "refused"],
)
def test_a_write_whose_outcome_is_unknown_still_counts_as_a_change(
    transport: httpx.MockTransport, reported: bool
) -> None:
    """A lost reply after a committed write must still trigger the backup pull."""
    from meridian.control.client import ControlServiceError, add_mutation_listener

    seen: list[str] = []
    stop = add_mutation_listener(seen.append)
    client = ControlClient("https://198.51.100.10/secret", ADMIN_TOKEN)
    client._client = httpx.Client(base_url="https://198.51.100.10/secret/", transport=transport)
    try:
        with client, pytest.raises(ControlServiceError):
            client.put_user("bob")
    finally:
        stop()

    assert seen == (["https://198.51.100.10/secret"] if reported else [])
