"""Failure-safety tests for ``meridian teardown``."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.commands.uninstall import _remove_saved_server_or_fail, _topology_references, run
from meridian.servers import ServerEntry
from meridian.setup.single_server import single_server_intent
from tests.support.topology import single_exit_intent


def test_teardown_bad_server_selector_fails_without_prompt_fallback() -> None:
    registry = MagicMock()

    with (
        patch("meridian.commands.uninstall.ServerRegistry", return_value=registry),
        patch("meridian.commands.uninstall.resolve_server", side_effect=typer.Exit(2)) as resolve,
        pytest.raises(typer.Exit) as exc_info,
    ):
        run("typo", yes=True)

    assert exc_info.value.exit_code == 2
    resolve.assert_called_once_with(registry, explicit_ip="typo", user="", port=0)


def test_teardown_declined_confirmation_exits_one() -> None:
    cluster = ClusterConfig()
    resolved = SimpleNamespace(ip="198.51.100.10", user="root", conn=MagicMock())

    with (
        patch("meridian.commands.uninstall.ServerRegistry"),
        patch("meridian.commands.uninstall.resolve_server", return_value=resolved),
        patch("meridian.cluster.ClusterConfig.load", return_value=cluster),
        patch("meridian.commands.uninstall.confirm", return_value=False),
        patch("meridian.commands.uninstall.ensure_server_connection") as ensure_connection,
        pytest.raises(typer.Exit) as exc_info,
    ):
        run(ip=resolved.ip)

    assert exc_info.value.exit_code == 1
    ensure_connection.assert_not_called()


def test_teardown_reports_saved_profile_failure_after_remote_completion() -> None:
    registry = MagicMock()
    registry.remove.side_effect = OSError("disk full")

    with pytest.raises(typer.Exit) as exc_info:
        _remove_saved_server_or_fail(registry, "198.51.100.10")

    assert exc_info.value.exit_code == 3


@pytest.mark.parametrize("failure_point", ["backup", "unlink"])
def test_panel_teardown_local_state_failure_reports_remote_completion(failure_point: str) -> None:
    cluster = ClusterConfig(
        panel=PanelConfig(
            url="https://198.51.100.1/panel",
            api_token="token",
            server_ip="198.51.100.1",
        ),
        topology_intent=single_server_intent(None, server_ref="srv-only"),
    )
    registry = MagicMock()
    registry.find.return_value = ServerEntry(id="srv-only", host="198.51.100.1")
    resolved = SimpleNamespace(ip="198.51.100.1", user="root", conn=MagicMock())
    cluster_path = MagicMock()
    if failure_point == "unlink":
        cluster_path.unlink.side_effect = OSError("permission denied")
    backup_error = OSError("disk full") if failure_point == "backup" else None

    with (
        patch("meridian.commands.uninstall.teardown_backup.status", return_value=None),
        patch(
            "meridian.provision.uninstall.Uninstall.preflight",
            return_value=SimpleNamespace(status="ok", detail="ready"),
        ),
        patch("meridian.commands.uninstall.ServerRegistry", return_value=registry),
        patch("meridian.commands.uninstall.resolve_server", return_value=resolved),
        patch("meridian.commands.uninstall.ensure_server_connection", return_value=resolved),
        patch("meridian.cluster.ClusterConfig.load", return_value=cluster),
        patch("meridian.config.CLUSTER_CONFIG", cluster_path),
        patch.object(cluster, "backup", side_effect=backup_error),
        patch("meridian.provision.steps.Provisioner") as provisioner,
        pytest.raises(typer.Exit) as exc_info,
    ):
        provisioner.return_value.run.return_value = [SimpleNamespace(status="changed", detail="")]
        run(ip=resolved.ip, yes=True, discard_backup=True)

    assert exc_info.value.exit_code == 3
    provisioner.return_value.run.assert_called_once()
    registry.remove.assert_not_called()


def test_v4_teardown_fails_before_ssh_or_remote_mutation() -> None:
    cluster = ClusterConfig(topology_intent=single_exit_intent())
    registry = MagicMock()
    registry.find.return_value = ServerEntry(
        id="srv-exit",
        host="198.51.100.2",
        name="exit-server",
    )
    resolved = SimpleNamespace(ip="198.51.100.2", user="root", conn=MagicMock())

    with (
        patch("meridian.commands.uninstall.ServerRegistry", return_value=registry),
        patch("meridian.commands.uninstall.resolve_server", return_value=resolved),
        patch("meridian.commands.uninstall.ensure_server_connection") as ensure_connection,
        patch("meridian.commands.uninstall.confirm") as confirmation,
        patch("meridian.cluster.ClusterConfig.load", return_value=cluster),
        patch("meridian.provision.steps.Provisioner") as provisioner,
        pytest.raises(typer.Exit),
    ):
        run(ip=resolved.ip, yes=False)

    ensure_connection.assert_not_called()
    confirmation.assert_not_called()
    provisioner.assert_not_called()
    registry.remove.assert_not_called()


def test_teardown_of_a_remnawave_fleet_server_points_at_migrate_or_its_own_release() -> None:
    """`setup` refuses a Remnawave fleet, so the usual hint would loop; its own release still edits it."""
    intent = single_exit_intent()
    intent = intent.model_copy(update={"control": intent.control.model_copy(update={"backend": "remnawave"})})
    cluster = ClusterConfig(panel=PanelConfig(deployed_with="4.0.0b8"), topology_intent=intent)
    registry = MagicMock()
    registry.find.return_value = ServerEntry(id="srv-exit", host="198.51.100.2", name="exit-server")
    resolved = SimpleNamespace(ip="198.51.100.2", user="root", conn=MagicMock())

    with (
        patch("meridian.commands.uninstall.ServerRegistry", return_value=registry),
        patch("meridian.commands.uninstall.resolve_server", return_value=resolved),
        patch("meridian.cluster.ClusterConfig.load", return_value=cluster),
        patch("meridian.commands.uninstall.fail", side_effect=typer.Exit(2)) as failure,
        pytest.raises(typer.Exit),
    ):
        run(ip=resolved.ip, yes=True)

    hint = failure.call_args.kwargs["hint"]
    assert "meridian migrate" in hint
    assert "uvx --from 'meridian-vpn==4.0.0b8' meridian setup" in hint


def test_v4_teardown_of_a_single_server_fleet_removes_everything(tmp_home) -> None:
    from meridian.setup.single_server import single_server_intent

    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.2/panel", api_token="token", server_ip="198.51.100.2"),
        topology_intent=single_server_intent(None, server_ref="srv-only"),
    )
    (tmp_home / "cluster.yml").write_text("version: 3\n")
    (tmp_home / "setup.json").write_text("{}")
    registry = MagicMock()
    registry.find.return_value = ServerEntry(id="srv-only", host="198.51.100.2")
    resolved = SimpleNamespace(ip="198.51.100.2", user="root", conn=MagicMock())
    provisioner = MagicMock()
    provisioner.return_value.run.return_value = [SimpleNamespace(status="changed", detail="")]

    with (
        patch("meridian.commands.uninstall.teardown_backup.status", return_value=None),
        patch(
            "meridian.provision.uninstall.Uninstall.preflight",
            return_value=SimpleNamespace(status="ok", detail="ready"),
        ),
        patch("meridian.commands.uninstall.ServerRegistry", return_value=registry),
        patch("meridian.commands.uninstall.resolve_server", return_value=resolved),
        patch("meridian.commands.uninstall.ensure_server_connection", return_value=resolved),
        patch("meridian.cluster.ClusterConfig.load", return_value=cluster),
        patch.object(cluster, "backup"),
        patch("meridian.provision.steps.Provisioner", provisioner),
    ):
        run(ip=resolved.ip, yes=True, discard_backup=True)

    provisioner.return_value.run.assert_called_once()
    assert not (tmp_home / "cluster.yml").exists()
    assert not (tmp_home / "setup.json").exists()
    registry.remove.assert_called_once_with(resolved.ip)


def test_v4_teardown_allows_server_after_role_and_workload_are_retired() -> None:
    cluster = ClusterConfig(topology_intent=single_exit_intent(exit_ref="srv-current-exit"))
    retired = ServerEntry(id="srv-retired", host="198.51.100.9", name="retired")

    assert _topology_references(cluster, retired) == []


def _roleless_server_cluster() -> ClusterConfig:
    return ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.1/panel", api_token="token", server_ip="198.51.100.1"),
        topology_intent=single_server_intent(None, server_ref="srv-control"),
    )


def test_roleless_server_teardown_removes_its_saved_profile() -> None:
    cluster = _roleless_server_cluster()
    registry = MagicMock()
    registry.find.return_value = ServerEntry(id="srv-spare", host="198.51.100.2")
    resolved = SimpleNamespace(ip="198.51.100.2", user="root", conn=MagicMock())
    with (
        patch("meridian.commands.uninstall.ServerRegistry", return_value=registry),
        patch("meridian.commands.uninstall.resolve_server", return_value=resolved),
        patch("meridian.commands.uninstall.ensure_server_connection", return_value=resolved),
        patch("meridian.cluster.ClusterConfig.load", return_value=cluster),
        patch("meridian.provision.steps.Provisioner") as provisioner,
    ):
        provisioner.return_value.run.return_value = [SimpleNamespace(status="changed", detail="")]
        run(ip=resolved.ip, yes=True)

    registry.remove.assert_called_once_with("198.51.100.2")


def test_cluster_without_topology_is_refused_before_ssh() -> None:
    cluster = ClusterConfig(panel=PanelConfig(url="https://198.51.100.1/panel", api_token="token"))
    registry = MagicMock()
    resolved = SimpleNamespace(ip="198.51.100.1", user="root", conn=MagicMock())

    with (
        patch("meridian.commands.uninstall.ServerRegistry", return_value=registry),
        patch("meridian.commands.uninstall.resolve_server", return_value=resolved),
        patch("meridian.commands.uninstall.ensure_server_connection") as ensure_connection,
        patch("meridian.cluster.ClusterConfig.load", return_value=cluster),
        pytest.raises(typer.Exit) as exc_info,
    ):
        run(ip=resolved.ip, yes=True)

    assert exc_info.value.exit_code == 2
    ensure_connection.assert_not_called()
    registry.remove.assert_not_called()


@pytest.mark.parametrize("case", ["older service", "local only", "incomplete", "concurrent change", "confirmed"])
def test_teardown_requires_a_current_off_server_save(case):
    from meridian.commands import teardown_backup
    from meridian.control.backup_models import BackupStatus

    def backup(changes, saved, complete=True):
        return BackupStatus(
            changes=changes,
            last_saved_changes=saved,
            last_saved_at="2026-11-01T10:00:00Z" if saved is not None else None,
            has_deployer_state=complete,
            has_recovery_key=True,
        )

    before = None if case == "older service" else backup(3, 2)
    after = {
        "older service": None,
        "local only": backup(3, None),
        "incomplete": backup(3, 3, False),
        "concurrent change": backup(4, 3),
        "confirmed": backup(3, 3),
    }[case]
    cluster = ClusterConfig()
    with (
        patch.object(teardown_backup, "pull_snapshot", return_value=SimpleNamespace(receipt="unavailable")) as pull,
        patch.object(teardown_backup, "status", return_value=after),
    ):
        if case == "confirmed":
            assert teardown_backup.confirmed_generation(cluster, before) == 3
        else:
            with pytest.raises(typer.Exit):
                teardown_backup.confirmed_generation(cluster, before)
        pull.assert_called_once()


def test_current_saved_copy_does_not_require_access_to_the_original_laptop():
    from meridian.commands import teardown_backup
    from meridian.control.backup_models import BackupStatus

    confirmed = BackupStatus(
        changes=8,
        last_saved_changes=8,
        last_saved_at="2026-11-01T10:00:00Z",
        has_deployer_state=True,
        has_recovery_key=True,
    )
    with patch.object(teardown_backup, "pull_snapshot") as pull:
        assert teardown_backup.confirmed_generation(ClusterConfig(), confirmed) == 8
    pull.assert_not_called()


def test_unattended_yes_is_not_consent_to_lose_the_live_database():
    cluster = _roleless_server_cluster()
    registry = MagicMock()
    registry.find.return_value = ServerEntry(id="srv-control", host="198.51.100.1")
    resolved = SimpleNamespace(ip="198.51.100.1", user="root", conn=MagicMock())
    with (
        patch("meridian.commands.uninstall.ServerRegistry", return_value=registry),
        patch("meridian.commands.uninstall.resolve_server", return_value=resolved),
        patch("meridian.commands.uninstall.ensure_server_connection", return_value=resolved),
        patch("meridian.cluster.ClusterConfig.load", return_value=cluster),
        patch("meridian.commands.teardown_backup.status", return_value=None),
        patch(
            "meridian.provision.uninstall.Uninstall.preflight",
            return_value=SimpleNamespace(status="ok", detail="ready"),
        ),
        patch("meridian.commands.teardown_backup.pull_snapshot", side_effect=OSError("disk full")),
        patch("meridian.provision.steps.Provisioner") as destructive,
        pytest.raises(typer.Exit) as refusal,
    ):
        run(ip=resolved.ip, yes=True)
    assert refusal.value.exit_code == 2
    destructive.assert_not_called()
    registry.remove.assert_not_called()


def test_remote_preflight_refusal_never_registers_a_backup_device_or_replaces_the_deposit():
    cluster = _roleless_server_cluster()
    registry = MagicMock()
    registry.find.return_value = ServerEntry(id="srv-control", host="198.51.100.1")
    resolved = SimpleNamespace(ip="198.51.100.1", user="root", conn=MagicMock())
    with (
        patch("meridian.commands.uninstall.ServerRegistry", return_value=registry),
        patch("meridian.commands.uninstall.resolve_server", return_value=resolved),
        patch("meridian.commands.uninstall.ensure_server_connection", return_value=resolved),
        patch("meridian.cluster.ClusterConfig.load", return_value=cluster),
        patch("meridian.commands.teardown_backup.status", return_value=None),
        patch(
            "meridian.provision.uninstall.Uninstall.preflight",
            return_value=SimpleNamespace(status="failed", detail="Stale topology or shared nginx"),
        ),
        patch("meridian.commands.teardown_backup.pull_snapshot") as pull,
        patch("meridian.provision.steps.Provisioner") as cleanup,
        pytest.raises(typer.Exit),
    ):
        run(ip=resolved.ip, yes=True)
    pull.assert_not_called()
    cleanup.assert_not_called()
