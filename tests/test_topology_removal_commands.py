"""`node remove`, `relay remove`, and `relay check` as topology intent edits plus reviewed apply."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.cluster import ClusterConfig, ManagedResourceBinding
from meridian.commands.node import run_remove as node_remove
from meridian.commands.relay import run_remove as relay_remove
from meridian.commands.relay_check import run_check
from meridian.console import set_json_mode
from meridian.core.topology import (
    AccessIntent,
    ControlPlaneIntent,
    EgressPoolIntent,
    ExitIntent,
    OrderedRouteIntent,
    ProtocolPathIntent,
    RoutingGatewayIntent,
    SetupIntent,
    TransparentRelayIntent,
)
from meridian.reconciler.resources import ResourceExecutionResult
from meridian.servers import ServerEntry, ServerRegistry
from tests.support.mock_connection import MockConnection

SERVERS = {
    "srv-control": ("198.51.100.10", "control"),
    "srv-exit-a": ("198.51.100.11", "exit-a-host"),
    "srv-exit-b": ("198.51.100.12", "exit-b-host"),
    "srv-relay-edge": ("198.51.100.13", "relay-edge"),
    "srv-relay-core": ("198.51.100.14", "relay-core"),
    "srv-gateway": ("198.51.100.15", "gateway"),
}


def _intent() -> SetupIntent:
    return SetupIntent(
        control=ControlPlaneIntent(server_ref="srv-control"),
        exits=[
            ExitIntent(
                id="exit-a",
                server_ref="srv-exit-a",
                paths=[ProtocolPathIntent(id="reality-a", protocol="reality", reality_sni="www.example.com")],
            ),
            ExitIntent(
                id="exit-b",
                server_ref="srv-exit-b",
                paths=[ProtocolPathIntent(id="reality-b", protocol="reality", reality_sni="www.example.org")],
            ),
        ],
        transparent_relays=[
            TransparentRelayIntent(
                id="relay-a",
                hop_server_refs=["srv-relay-edge", "srv-relay-core"],
                exit_ref="exit-b",
                protocol_path_ref="reality-b",
            )
        ],
        routing_gateways=[RoutingGatewayIntent(id="gateway-a", server_ref="srv-gateway", bridge_path_ref="reality-a")],
        egress_pools=[EgressPoolIntent(id="primary", exit_refs=["exit-a", "exit-b"])],
        routes=[
            OrderedRouteIntent(
                id="regional",
                priority=10,
                match="country",
                match_values=["RU"],
                target_ref="primary",
                source_gateway_ref="gateway-a",
            )
        ],
        default_egress_ref="primary",
        access=AccessIntent(users=["default"]),
    )


@pytest.fixture
def registry(servers_file: Path) -> ServerRegistry:
    saved = ServerRegistry(servers_file)
    for server_id, (host, name) in SERVERS.items():
        saved.add(ServerEntry(host=host, name=name, id=server_id))
    return saved


def _remove_node(query: str, intent: SetupIntent | None = None, *, force: bool = False) -> MagicMock:
    cluster = ClusterConfig(topology_intent=intent or _intent())
    with (
        patch("meridian.commands.node.load_topology_cluster", return_value=cluster),
        patch("meridian.commands.topology_removal.apply_topology_removal") as apply,
    ):
        node_remove(query, yes=True, force=force)
    return apply


def _remove_relay(query: str, *, exit_arg: str = "") -> MagicMock:
    cluster = ClusterConfig(topology_intent=_intent())
    with (
        patch("meridian.commands.relay.load_topology_cluster", return_value=cluster),
        patch("meridian.commands.topology_removal.apply_topology_removal") as apply,
    ):
        relay_remove(query, exit_arg=exit_arg, yes=True)
    return apply


def _applied_intent(apply: MagicMock) -> SetupIntent:
    apply.assert_called_once()
    updated = apply.call_args.args[1]
    assert isinstance(updated, SetupIntent)
    return updated


class TestNodeRemove:
    def test_control_server_points_to_teardown(self, registry: ServerRegistry) -> None:
        with pytest.raises(typer.Exit) as exc_info:
            _remove_node("198.51.100.10")
        assert exc_info.value.exit_code == 2

    def test_exit_with_relays_needs_force(self, registry: ServerRegistry) -> None:
        with pytest.raises(typer.Exit) as exc_info:
            _remove_node("exit-b-host")
        assert exc_info.value.exit_code == 2

    def test_force_also_drops_relay_chains_into_the_exit(self, registry: ServerRegistry) -> None:
        apply = _remove_node("198.51.100.12", force=True)

        updated = _applied_intent(apply)
        assert [exit_.id for exit_ in updated.exits] == ["exit-a"]
        assert updated.transparent_relays == []
        assert updated.egress_pools[0].exit_refs == ["exit-a"]
        assert apply.call_args.kwargs["subject"] == "exit exit-b on exit-b-host"
        assert apply.call_args.kwargs["extra_lines"] == ("Also removes relay chains: relay-a",)
        assert apply.call_args.kwargs["yes"] is True

    def test_exit_that_bridges_a_gateway_is_refused(self, registry: ServerRegistry) -> None:
        with pytest.raises(typer.Exit) as exc_info:
            _remove_node("exit-a")
        assert exc_info.value.exit_code == 2

    def test_exit_still_named_as_default_egress_is_refused(self, registry: ServerRegistry) -> None:
        intent = _intent().model_copy(update={"default_egress_ref": "exit-b"})
        with pytest.raises(typer.Exit) as exc_info:
            _remove_node("exit-b", intent, force=True)
        assert exc_info.value.exit_code == 2

    def test_gateway_removal_drops_its_scoped_routes(self, registry: ServerRegistry) -> None:
        apply = _remove_node("gateway")

        updated = _applied_intent(apply)
        assert updated.routing_gateways == []
        assert updated.routes == []
        assert apply.call_args.kwargs["subject"] == "routing gateway gateway-a on gateway"
        assert apply.call_args.kwargs["extra_lines"] == ("Also drops routing rules scoped to it: regional",)

    def test_relay_hop_points_to_relay_remove(self, registry: ServerRegistry) -> None:
        with pytest.raises(typer.Exit) as exc_info:
            _remove_node("198.51.100.13")
        assert exc_info.value.exit_code == 2

    def test_unknown_node_is_not_found(self, registry: ServerRegistry) -> None:
        with pytest.raises(typer.Exit) as exc_info:
            _remove_node("198.51.100.99")
        assert exc_info.value.exit_code == 2


class TestRelayRemove:
    @pytest.mark.parametrize("query", ["198.51.100.14", "relay-edge"])
    def test_relay_server_drops_every_chain_through_it(self, registry: ServerRegistry, query: str) -> None:
        apply = _remove_relay(query)

        assert _applied_intent(apply).transparent_relays == []
        assert apply.call_args.kwargs["subject"].startswith("relay chain relay-a through ")

    def test_chain_id_selects_the_chain(self, registry: ServerRegistry) -> None:
        apply = _remove_relay("relay-a")

        assert _applied_intent(apply).transparent_relays == []
        assert apply.call_args.kwargs["subject"] == "relay chain relay-a"

    def test_exit_filter_must_match(self, registry: ServerRegistry) -> None:
        with pytest.raises(typer.Exit) as exc_info:
            _remove_relay("relay-a", exit_arg="exit-a")
        assert exc_info.value.exit_code == 2

    def test_unknown_relay_is_not_found(self, registry: ServerRegistry) -> None:
        with pytest.raises(typer.Exit) as exc_info:
            _remove_relay("198.51.100.99")
        assert exc_info.value.exit_code == 2


class TestRelayCheck:
    @staticmethod
    def _check(
        query: str,
        connections: dict[str, MockConnection],
        *,
        listener_open: bool = True,
    ) -> int:
        cluster = ClusterConfig(topology_intent=_intent())
        with (
            patch("meridian.commands.relay_check.load_topology_cluster", return_value=cluster),
            patch("meridian.diagnostics.relay.tcp_connect", return_value=listener_open),
        ):
            try:
                run_check(query, connection_factory=lambda entry: connections[entry.host])
            except typer.Exit as exc:
                return int(exc.exit_code)
        return 0

    @staticmethod
    def _hop(probe_status: int = 0, leg_bytes: int = 98304, leg_status: int = 0) -> MockConnection:
        conn = MockConnection().when("systemctl is-active", stdout="active\n")
        conn.when("curl -sk", stdout=f"leg=0.012|0.140|{leg_bytes}\nleg-status={leg_status}\n")
        return conn.when("/dev/tcp", stdout=f"probe-status={probe_status}\n")

    @classmethod
    def _healthy(cls) -> MockConnection:
        return cls._hop()

    def test_every_hop_passing_exits_zero(self, registry: ServerRegistry) -> None:
        edge, core = self._healthy(), self._healthy()

        assert self._check("relay-a", {"198.51.100.13": edge, "198.51.100.14": core}) == 0
        assert any("/dev/tcp/198.51.100.14/" in command for command in edge._calls)
        assert any("/dev/tcp/198.51.100.12/" in command for command in core._calls)
        assert not any(" nc " in command for command in edge._calls + core._calls)

    def test_server_query_checks_only_its_hops(self, registry: ServerRegistry) -> None:
        core = self._healthy()

        assert self._check("198.51.100.14", {"198.51.100.14": core}) == 0
        assert len(core._calls) == 3

    def test_each_hop_pulls_the_chain_camouflage_name_through_its_next_hop(self, registry: ServerRegistry) -> None:
        edge, core = self._healthy(), self._healthy()

        assert self._check("relay-a", {"198.51.100.13": edge, "198.51.100.14": core}) == 0
        (edge_leg,) = [command for command in edge._calls if "curl -sk" in command]
        assert "--connect-to www.example.org:443:198.51.100.14:" in edge_leg
        assert "https://www.example.org/" in edge_leg

    def test_frozen_relay_leg_is_a_finding(self, registry: ServerRegistry) -> None:
        frozen = self._hop(leg_bytes=16384, leg_status=28)

        assert self._check("relay-a", {"198.51.100.13": self._healthy(), "198.51.100.14": frozen}) == 4

    def test_closed_next_hop_is_negative_evidence(self, registry: ServerRegistry) -> None:
        core = self._hop(probe_status=1)

        assert self._check("relay-a", {"198.51.100.13": self._healthy(), "198.51.100.14": core}) == 4

    def test_closed_public_listener_is_negative_evidence(self, registry: ServerRegistry) -> None:
        connections = {"198.51.100.13": self._healthy(), "198.51.100.14": self._healthy()}

        assert self._check("relay-a", connections, listener_open=False) == 4

    def test_ssh_failure_is_inconclusive_json(
        self,
        registry: ServerRegistry,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        unreachable = MockConnection().when("", rc=255)
        set_json_mode(True)
        try:
            code = self._check("relay-a", {"198.51.100.13": self._healthy(), "198.51.100.14": unreachable})
        finally:
            set_json_mode(False)

        assert code == 3
        payload = json.loads(capsys.readouterr().out)
        assert payload["command"] == "relay.check"
        assert payload["status"] == "failed"
        assert payload["errors"][0]["code"] == "MERIDIAN_RELAY_CHECK_INCONCLUSIVE"
        assert payload["data"]["verdict"] == "inconclusive"
        assert [hop["hop_index"] for hop in payload["data"]["hops"]] == [0, 1]

    def test_unknown_relay_fails_before_ssh(self, registry: ServerRegistry) -> None:
        assert self._check("198.51.100.99", {}) == 2


class _FakeRuntime:
    """Stands in for SetupRuntime: one review, one apply bound to its hash."""

    def __init__(self, result: ResourceExecutionResult) -> None:
        self.result = result
        self.applied_hash = ""
        self.retiring = (ManagedResourceBinding(logical_id="realm:relay-a:0", resource_kind="realm_hop", active=True),)

    def review(self, draft: object, *, cluster: object) -> SimpleNamespace:
        return SimpleNamespace(retiring=self.retiring, review_hash="sha256:review")

    def apply_intent(self, intent: SetupIntent, *, expected_plan_hash: str) -> ResourceExecutionResult:
        self.applied_hash = expected_plan_hash
        return self.result


class TestApplyTopologyRemoval:
    @staticmethod
    def _run(runtime: _FakeRuntime, *, yes: bool = True, confirmed: bool = True) -> int:
        from meridian.commands.topology_removal import apply_topology_removal

        with (
            patch("meridian.setup.runtime.SetupRuntime", return_value=runtime),
            patch("meridian.commands.topology_removal.confirm", return_value=confirmed),
        ):
            try:
                apply_topology_removal(ClusterConfig(), _intent(), subject="relay chain relay-a", yes=yes)
            except typer.Exit as exc:
                return int(exc.exit_code)
        return 0

    def test_applies_exactly_the_reviewed_removal(self, capsys: pytest.CaptureFixture[str]) -> None:
        runtime = _FakeRuntime(ResourceExecutionResult(plan_hash="sha256:plan", generation=2, retired=["a", "b"]))

        assert self._run(runtime) == 0
        assert runtime.applied_hash == "sha256:review"
        output = capsys.readouterr().err
        assert "remove realm:relay-a:0" in output
        assert "2 resources cleaned up" in output

    def test_declined_confirmation_changes_nothing(self) -> None:
        runtime = _FakeRuntime(ResourceExecutionResult(plan_hash="sha256:plan", generation=2))

        assert self._run(runtime, yes=False, confirmed=False) == 1
        assert runtime.applied_hash == ""

    def test_failed_retirement_is_a_system_failure(self, capsys: pytest.CaptureFixture[str]) -> None:
        result = ResourceExecutionResult(
            plan_hash="sha256:plan",
            generation=2,
            retirement_errors=["retire realm:relay-a:0: SSH unreachable"],
        )

        assert self._run(_FakeRuntime(result)) == 3
        assert "meridian apply" in capsys.readouterr().err
