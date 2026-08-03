"""Failure-safety tests for ``meridian teardown``."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.cluster import ClusterConfig, NodeEntry, PanelConfig, RelayEntry
from meridian.commands.uninstall import _remove_saved_server_or_fail, _v4_target_references, run
from meridian.core.topology import AccessIntent, ControlPlaneIntent, ExitIntent, ProtocolPathIntent, SetupIntent
from meridian.remnawave import RemnawaveError
from meridian.servers import ServerEntry


def test_teardown_bad_server_selector_fails_without_prompt_fallback() -> None:
    registry = MagicMock()

    with (
        patch("meridian.commands.uninstall.ServerRegistry", return_value=registry),
        patch("meridian.commands.uninstall.resolve_server", side_effect=typer.Exit(2)) as resolve,
        pytest.raises(typer.Exit) as exc_info,
    ):
        run(requested_server="typo", yes=True)

    assert exc_info.value.exit_code == 2
    resolve.assert_called_once_with(registry, requested_server="typo", explicit_ip="", user="root")


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


def test_teardown_refuses_ambiguous_legacy_node_and_relay_role() -> None:
    ip = "198.51.100.20"
    cluster = ClusterConfig(
        nodes=[NodeEntry(ip=ip, name="dual-role")],
        relays=[RelayEntry(ip=ip, exit_node_ip="198.51.100.10", name="relay-role")],
    )
    registry = MagicMock()
    resolved = SimpleNamespace(ip=ip, user="root", conn=MagicMock())

    with (
        patch("meridian.commands.uninstall.ServerRegistry", return_value=registry),
        patch("meridian.commands.uninstall.resolve_server", return_value=resolved),
        patch("meridian.cluster.ClusterConfig.load", return_value=cluster),
        patch("meridian.commands.relay.run_remove") as remove_relay,
        pytest.raises(typer.Exit) as exc_info,
    ):
        run(ip=ip, yes=True)

    assert exc_info.value.exit_code == 2
    remove_relay.assert_not_called()
    registry.remove.assert_not_called()


def test_dependent_relay_cleanup_failure_retains_local_state() -> None:
    relay = RelayEntry(ip="198.51.100.20", exit_node_ip="198.51.100.1")
    panel_node = NodeEntry(ip="198.51.100.1", is_panel_host=True)
    cluster = ClusterConfig(
        panel=PanelConfig(
            url="https://198.51.100.1/panel",
            api_token="token",
            server_ip="198.51.100.1",
        ),
        nodes=[panel_node],
        relays=[relay],
    )
    registry = MagicMock()
    resolved = SimpleNamespace(ip="198.51.100.1", user="root", conn=MagicMock())
    relay_connection = MagicMock()

    with (
        patch("meridian.commands.uninstall.ServerRegistry", return_value=registry),
        patch("meridian.commands.uninstall.resolve_server", return_value=resolved),
        patch("meridian.commands.uninstall.ensure_server_connection", return_value=resolved),
        patch("meridian.cluster.ClusterConfig.load", return_value=cluster),
        patch("meridian.commands.uninstall.ServerConnection", return_value=relay_connection),
        patch("meridian.relay_ops.stop_relay_service", return_value=False) as stop_relay,
        patch("meridian.provision.steps.Provisioner") as provisioner,
        pytest.raises(typer.Exit),
    ):
        run(ip=resolved.ip, yes=True)

    assert cluster.relays == [relay]
    stop_relay.assert_not_called()
    registry.remove.assert_not_called()
    provisioner.assert_not_called()


def test_non_panel_teardown_removes_relays_then_panel_node() -> None:
    relay = RelayEntry(ip="198.51.100.20", exit_node_ip="198.51.100.2")
    node = NodeEntry(ip="198.51.100.2", uuid="550e8400-e29b-41d4-a716-446655440002")
    cluster = ClusterConfig(
        panel=PanelConfig(
            url="https://198.51.100.1/panel",
            api_token="token",
            server_ip="198.51.100.1",
        ),
        nodes=[node],
        relays=[relay],
    )
    registry = MagicMock()
    panel = MagicMock()
    panel.__enter__.return_value = panel
    resolved = SimpleNamespace(ip=node.ip, user="root", conn=MagicMock())

    def remove_relay_side_effect(cluster_arg: ClusterConfig, _panel: MagicMock, *, relay_ip: str) -> None:
        cluster_arg.relays = [candidate for candidate in cluster_arg.relays if candidate.ip != relay_ip]

    def remove_node_side_effect(
        cluster_arg: ClusterConfig,
        _panel: MagicMock,
        *,
        node_ip: str,
        **_kwargs: object,
    ) -> None:
        cluster_arg.nodes = [candidate for candidate in cluster_arg.nodes if candidate.ip != node_ip]

    with (
        patch("meridian.commands.uninstall.ServerRegistry", return_value=registry),
        patch("meridian.commands.uninstall.resolve_server", return_value=resolved),
        patch("meridian.commands.uninstall.ensure_server_connection", return_value=resolved),
        patch("meridian.cluster.ClusterConfig.load", return_value=cluster),
        patch("meridian.commands._helpers.make_panel", return_value=panel),
        patch("meridian.operations.remove_relay", side_effect=remove_relay_side_effect) as remove_relay,
        patch("meridian.operations.remove_node", side_effect=remove_node_side_effect) as remove_node,
        patch("meridian.provision.steps.Provisioner") as provisioner,
    ):
        provisioner.return_value.run.return_value = [SimpleNamespace(status="changed", detail="")]
        run(ip=resolved.ip, yes=True)

    remove_relay.assert_called_once_with(cluster, panel, relay_ip=relay.ip)
    remove_node.assert_called_once()
    assert cluster.relays == []
    assert cluster.nodes == []
    registry.remove.assert_called_once_with(node.ip)


def test_panel_cleanup_failure_after_uninstall_retains_registry_and_node() -> None:
    node = NodeEntry(ip="198.51.100.2", uuid="550e8400-e29b-41d4-a716-446655440002")
    cluster = ClusterConfig(
        panel=PanelConfig(
            url="https://198.51.100.1/panel",
            api_token="token",
            server_ip="198.51.100.1",
        ),
        nodes=[node],
    )
    registry = MagicMock()
    panel = MagicMock()
    panel.__enter__.return_value = panel
    resolved = SimpleNamespace(ip=node.ip, user="root", conn=MagicMock())

    with (
        patch("meridian.commands.uninstall.ServerRegistry", return_value=registry),
        patch("meridian.commands.uninstall.resolve_server", return_value=resolved),
        patch("meridian.commands.uninstall.ensure_server_connection", return_value=resolved),
        patch("meridian.cluster.ClusterConfig.load", return_value=cluster),
        patch("meridian.commands._helpers.make_panel", return_value=panel),
        patch("meridian.operations.remove_node", side_effect=RemnawaveError("panel unavailable")),
        patch("meridian.provision.steps.Provisioner") as provisioner,
        pytest.raises(typer.Exit),
    ):
        provisioner.return_value.run.return_value = [SimpleNamespace(status="changed", detail="")]
        run(ip=resolved.ip, yes=True)

    assert cluster.nodes == [node]
    registry.remove.assert_not_called()


@pytest.mark.parametrize("failure_point", ["backup", "unlink"])
def test_panel_teardown_local_state_failure_reports_remote_completion(failure_point: str) -> None:
    panel_node = NodeEntry(ip="198.51.100.1", is_panel_host=True)
    cluster = ClusterConfig(
        panel=PanelConfig(
            url="https://198.51.100.1/panel",
            api_token="token",
            server_ip=panel_node.ip,
        ),
        nodes=[panel_node],
    )
    registry = MagicMock()
    resolved = SimpleNamespace(ip=panel_node.ip, user="root", conn=MagicMock())
    cluster_path = MagicMock()
    if failure_point == "unlink":
        cluster_path.unlink.side_effect = OSError("permission denied")
    backup_error = OSError("disk full") if failure_point == "backup" else None

    with (
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
        run(ip=resolved.ip, yes=True)

    assert exc_info.value.exit_code == 3
    provisioner.return_value.run.assert_called_once()
    registry.remove.assert_not_called()


def test_v4_teardown_fails_before_ssh_or_remote_mutation() -> None:
    cluster = ClusterConfig(
        topology_intent=SetupIntent(
            control=ControlPlaneIntent(server_ref="srv-control"),
            exits=[
                ExitIntent(
                    id="exit-a",
                    server_ref="srv-exit",
                    paths=[
                        ProtocolPathIntent(
                            id="reality-a",
                            protocol="reality",
                            reality_sni="www.example.com",
                        )
                    ],
                )
            ],
            default_egress_ref="exit-a",
            access=AccessIntent(users=["alice"]),
        )
    )
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


def test_v4_teardown_allows_server_after_role_and_workload_are_retired() -> None:
    cluster = ClusterConfig(
        topology_intent=SetupIntent(
            control=ControlPlaneIntent(server_ref="srv-control"),
            exits=[
                ExitIntent(
                    id="exit-a",
                    server_ref="srv-current-exit",
                    paths=[
                        ProtocolPathIntent(
                            id="reality-a",
                            protocol="reality",
                            reality_sni="www.example.com",
                        )
                    ],
                )
            ],
            default_egress_ref="exit-a",
            access=AccessIntent(users=["alice"]),
        )
    )
    retired = ServerEntry(id="srv-retired", host="198.51.100.9", name="retired")

    assert _v4_target_references(cluster, retired) == []


def test_relay_target_uses_relay_removal_instead_of_node_uninstall() -> None:
    relay = RelayEntry(ip="198.51.100.20", exit_node_ip="198.51.100.1")
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.1/panel", api_token="token", server_ip="198.51.100.1"),
        relays=[relay],
    )
    registry = MagicMock()
    resolved = SimpleNamespace(ip=relay.ip, user="ubuntu", conn=MagicMock())

    with (
        patch("meridian.commands.uninstall.ServerRegistry", return_value=registry),
        patch("meridian.commands.uninstall.resolve_server", return_value=resolved),
        patch("meridian.cluster.ClusterConfig.load", return_value=cluster),
        patch("meridian.commands.relay.run_remove") as remove_relay,
        patch("meridian.commands.uninstall.ensure_server_connection") as ensure_connection,
        patch("meridian.provision.steps.Provisioner") as provisioner,
    ):
        run(ip=relay.ip, yes=True)

    remove_relay.assert_called_once_with(relay.ip, user="ubuntu", yes=True)
    registry.remove.assert_called_once_with(relay.ip)
    ensure_connection.assert_not_called()
    provisioner.assert_not_called()


def test_panel_teardown_refuses_to_orphan_other_nodes() -> None:
    panel_node = NodeEntry(ip="198.51.100.1", is_panel_host=True)
    exit_node = NodeEntry(ip="198.51.100.2", name="exit-two")
    cluster = ClusterConfig(
        panel=PanelConfig(
            url="https://198.51.100.1/panel",
            api_token="token",
            server_ip=panel_node.ip,
        ),
        nodes=[panel_node, exit_node],
    )
    registry = MagicMock()
    resolved = SimpleNamespace(ip=panel_node.ip, user="root", conn=MagicMock())

    with (
        patch("meridian.commands.uninstall.ServerRegistry", return_value=registry),
        patch("meridian.commands.uninstall.resolve_server", return_value=resolved),
        patch("meridian.commands.uninstall.ensure_server_connection") as ensure_connection,
        patch("meridian.cluster.ClusterConfig.load", return_value=cluster),
        patch("meridian.provision.steps.Provisioner") as provisioner,
        pytest.raises(typer.Exit),
    ):
        run(ip=resolved.ip, yes=True)

    ensure_connection.assert_not_called()
    provisioner.assert_not_called()
    registry.remove.assert_not_called()
