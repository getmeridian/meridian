"""Stateful two-exit tests for V4 Remnawave resource drivers."""

from __future__ import annotations

from collections import Counter
from typing import cast
from uuid import UUID

import pytest

from meridian.cluster import ClusterConfig, ManagedResourceBinding, RealityKeyBinding
from meridian.compiler import compile_topology
from meridian.compiler.models import ConfigProfilePayload
from meridian.core.topology import (
    AccessIntent,
    ControlPlaneIntent,
    ExitIntent,
    ProtocolPathIntent,
    SetupIntent,
)
from meridian.reconciler.remnawave_drivers import (
    RemnawaveDriverContext,
    build_remnawave_drivers,
)
from meridian.reconciler.resource_executor import execute_resource_plan
from meridian.reconciler.resources import (
    ResourceAction,
    ResourceApplyReceipt,
    ResourceObservation,
    ResourceReconcileError,
    build_resource_actions,
    postcondition_key,
)
from meridian.reconciler.workloads import WorkloadStateManager
from meridian.remnawave import (
    ConfigProfile,
    Host,
    Inbound,
    InternalSquad,
    MeridianPanel,
    Node,
    NodeCredentials,
)


class StatefulPanel:
    def __init__(self) -> None:
        self.profiles: dict[str, ConfigProfile] = {}
        self.nodes: dict[str, Node] = {}
        self.hosts: dict[str, Host] = {}
        self.squads: dict[str, InternalSquad] = {}
        self.calls: Counter[str] = Counter()
        self._next_id = 1

    def _uuid(self) -> str:
        value = str(UUID(int=self._next_id))
        self._next_id += 1
        return value

    def create_config_profile(self, name: str, config: dict) -> ConfigProfile:
        self.calls["create_profile"] += 1
        profile = ConfigProfile(uuid=self._uuid(), name=name, config=config)
        profile.inbounds = self._render_inbounds(profile)
        self.profiles[profile.uuid] = profile
        return profile

    def update_config_profile(
        self,
        uuid: str,
        *,
        name: str | None = None,
        config: dict | None = None,
    ) -> ConfigProfile:
        self.calls["update_profile"] += 1
        profile = self.profiles[uuid]
        if name is not None:
            profile.name = name
        if config is not None:
            previous = {inbound.tag: inbound.uuid for inbound in profile.inbounds}
            profile.config = config
            profile.inbounds = self._render_inbounds(profile, previous)
        return profile

    def get_config_profile(self, uuid: str) -> ConfigProfile | None:
        return self.profiles.get(uuid)

    def list_config_profiles(self) -> list[ConfigProfile]:
        return list(self.profiles.values())

    def _render_inbounds(
        self,
        profile: ConfigProfile,
        previous: dict[str, str] | None = None,
    ) -> list[Inbound]:
        rendered: list[Inbound] = []
        for raw in profile.config.get("inbounds", []):
            stream = raw.get("streamSettings", {})
            tag = raw["tag"]
            rendered.append(
                Inbound(
                    uuid=(previous or {}).get(tag, self._uuid()),
                    tag=tag,
                    type=raw["protocol"],
                    network=stream.get("network", ""),
                    security=stream.get("security", ""),
                    profile_uuid=profile.uuid,
                    port=raw["port"],
                )
            )
        return rendered

    def create_node(
        self,
        name: str,
        address: str,
        port: int,
        *,
        config_profile_uuid: str,
        inbound_uuids: list[str] | None = None,
        country_code: str = "XX",
    ) -> NodeCredentials:
        self.calls["create_node"] += 1
        uuid = self._uuid()
        self.nodes[uuid] = Node(
            uuid=uuid,
            name=name,
            address=address,
            port=port,
            active_config_profile_uuid=config_profile_uuid,
            active_inbound_uuids=list(inbound_uuids or []),
            country_code=country_code,
        )
        return NodeCredentials(uuid=uuid, secret_key=f"secret-{uuid}")

    def get_node(self, uuid: str) -> Node | None:
        return self.nodes.get(uuid)

    def list_nodes(self) -> list[Node]:
        return list(self.nodes.values())

    def bind_node_profile(
        self,
        node_uuid: str,
        profile_uuid: str,
        inbound_uuids: list[str],
    ) -> Node:
        self.calls["bind_node"] += 1
        node = self.nodes[node_uuid]
        node.active_config_profile_uuid = profile_uuid
        node.active_inbound_uuids = list(inbound_uuids)
        return node

    def update_node_name(self, uuid: str, name: str) -> None:
        self.calls["rename_node"] += 1
        self.nodes[uuid].name = name

    def enable_node(self, uuid: str) -> None:
        self.calls["enable_node"] += 1
        self.nodes[uuid].is_disabled = False

    def create_host(self, **kwargs: object) -> Host:
        self.calls["create_host"] += 1
        host = self._host_from_kwargs(self._uuid(), kwargs)
        self.hosts[host.uuid] = host
        return host

    def update_host(self, uuid: str, **kwargs: object) -> Host:
        self.calls["update_host"] += 1
        host = self._host_from_kwargs(uuid, kwargs)
        self.hosts[uuid] = host
        return host

    def get_host(self, uuid: str) -> Host | None:
        return self.hosts.get(uuid)

    def list_hosts(self) -> list[Host]:
        return list(self.hosts.values())

    def _host_from_kwargs(self, uuid: str, values: dict[str, object]) -> Host:
        return Host(
            uuid=uuid,
            remark=str(values["remark"]),
            address=str(values["address"]),
            port=int(cast(int, values["port"])),
            config_profile_uuid=str(values["config_profile_uuid"]),
            inbound_uuid=str(values["inbound_uuid"]),
            sni=str(values["sni"]),
            host=str(values["host_header"]),
            path=str(values["path"]),
            alpn=str(values["alpn"] or ""),
            fingerprint=str(values["fingerprint"] or ""),
            security_layer=str(values["security_layer"]),
            is_disabled=bool(values["is_disabled"]),
        )

    def create_internal_squad(self, name: str, inbound_uuids: list[str]) -> InternalSquad:
        self.calls["create_squad"] += 1
        squad = InternalSquad(uuid=self._uuid(), name=name, inbound_uuids=list(inbound_uuids))
        self.squads[squad.uuid] = squad
        return squad

    def update_internal_squad(
        self,
        uuid: str,
        *,
        name: str | None = None,
        inbound_uuids: list[str] | None = None,
    ) -> InternalSquad:
        self.calls["update_squad"] += 1
        squad = self.squads[uuid]
        if name is not None:
            squad.name = name
        if inbound_uuids is not None:
            squad.inbound_uuids = list(inbound_uuids)
        return squad

    def get_internal_squad(self, uuid: str) -> InternalSquad | None:
        return self.squads.get(uuid)

    def list_internal_squads(self) -> list[InternalSquad]:
        return list(self.squads.values())


class AlreadyConvergedDriver:
    def observe(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceObservation:
        return ResourceObservation(
            exists=True,
            observed_hash=action.expected_hash,
            remote_id=action.resource.logical_id,
            satisfied_postconditions=sorted(
                postcondition_key(item.kind, item.target_ref, item.detail)
                for item in action.resource.postconditions
            ),
        )

    def apply(
        self,
        action: ResourceAction,
        binding: ManagedResourceBinding | None,
    ) -> ResourceApplyReceipt:
        raise AssertionError("converged test driver must never mutate")


def _intent() -> SetupIntent:
    return SetupIntent(
        control=ControlPlaneIntent(server_ref="srv-control"),
        exits=[
            ExitIntent(
                id="exit-a",
                server_ref="srv-exit-a",
                paths=[
                    ProtocolPathIntent(
                        id="reality-a",
                        protocol="reality",
                        reality_sni="www.microsoft.com",
                    ),
                    ProtocolPathIntent(
                        id="xhttp-a",
                        protocol="xhttp",
                        tls_sni="vpn-a.example.com",
                        host="vpn-a.example.com",
                        path="xhttp-a",
                    ),
                ],
            ),
            ExitIntent(
                id="exit-b",
                server_ref="srv-exit-b",
                paths=[
                    ProtocolPathIntent(
                        id="reality-b",
                        protocol="reality",
                        reality_sni="www.cloudflare.com",
                    ),
                    ProtocolPathIntent(
                        id="wss-b",
                        protocol="wss",
                        tls_sni="vpn-b.example.com",
                        host="vpn-b.example.com",
                        path="ws-b",
                    ),
                    ProtocolPathIntent(
                        id="hy2-b",
                        protocol="hysteria2",
                        tls_sni="vpn-b.example.com",
                    ),
                ],
                warp=True,
            ),
        ],
        default_egress_ref="exit-a",
        access=AccessIntent(users=["default"]),
    )


def _keys(server_ref: str) -> RealityKeyBinding:
    suffix = server_ref.removeprefix("srv-")
    return RealityKeyBinding(
        public_key=f"public-{suffix}",
        private_key=f"private-{suffix}",
        short_id=f"short-{suffix}",
    )


def test_two_exits_reconcile_distinct_profiles_nodes_hosts_and_squad() -> None:
    plan = compile_topology(_intent())
    cluster = ClusterConfig()
    panel = StatefulPanel()
    key_calls: list[str] = []
    workloads = WorkloadStateManager(
        cluster,
        persist=lambda _state: None,
        key_factory=lambda server_ref: (key_calls.append(server_ref), _keys(server_ref))[1],
    )
    context = RemnawaveDriverContext(
        panel=cast(MeridianPanel, panel),
        plan=plan,
        cluster=cluster,
        workloads=workloads,
        server_addresses={
            "srv-control": "198.51.100.10",
            "srv-exit-a": "198.51.100.20",
            "srv-exit-b": "198.51.100.30",
        },
    )
    fallback = AlreadyConvergedDriver()
    drivers = {kind: fallback for kind in {resource.payload.kind for resource in plan.resources}}
    drivers.update(build_remnawave_drivers(context))

    result = execute_resource_plan(
        plan,
        cluster,
        drivers,
        persist=lambda _state: None,
    )

    assert result.all_succeeded
    assert sorted(key_calls) == ["srv-exit-a", "srv-exit-b"]
    assert len(panel.profiles) == 2
    profiles = {profile.name: profile for profile in panel.profiles.values()}
    exit_a_profile = profiles["Meridian v4 / exit-a"]
    exit_b_profile = profiles["Meridian v4 / exit-b"]
    assert [item.tag for item in exit_a_profile.inbounds] == [
        "meridian-exit-a-reality",
        "meridian-exit-a-xhttp",
    ]
    assert [item.tag for item in exit_b_profile.inbounds] == [
        "meridian-exit-b-hysteria2",
        "meridian-exit-b-reality",
        "meridian-exit-b-wss",
    ]
    assert (
        exit_a_profile.config["inbounds"][0]["streamSettings"]["realitySettings"]["privateKey"]
        == "private-exit-a"
    )
    assert (
        exit_b_profile.config["inbounds"][1]["streamSettings"]["realitySettings"]["privateKey"]
        == "private-exit-b"
    )
    assert [item["tag"] for item in exit_a_profile.config["outbounds"]] == ["direct", "block"]
    assert [item["tag"] for item in exit_b_profile.config["outbounds"]] == [
        "warp",
        "direct",
        "block",
    ]

    assert len(panel.nodes) == 2
    nodes = {node.name: node for node in panel.nodes.values()}
    assert nodes["Meridian v4 / exit-a"].active_config_profile_uuid == exit_a_profile.uuid
    assert nodes["Meridian v4 / exit-b"].active_config_profile_uuid == exit_b_profile.uuid
    assert set(nodes["Meridian v4 / exit-a"].active_inbound_uuids) == {
        item.uuid for item in exit_a_profile.inbounds
    }
    assert set(nodes["Meridian v4 / exit-b"].active_inbound_uuids) == {
        item.uuid for item in exit_b_profile.inbounds
    }

    assert len(panel.hosts) == 5
    hosts = {host.remark: host for host in panel.hosts.values()}
    assert hosts["Meridian v4 / exit-a / xhttp-a / direct"].path == "/xhttp-a"
    assert hosts["Meridian v4 / exit-b / wss-b / direct"].path == "/ws-b"
    assert hosts["Meridian v4 / exit-b / hy2-b / direct"].alpn == "h3"
    assert hosts["Meridian v4 / exit-a / reality-a / direct"].fingerprint == "chrome"
    assert all(not host.is_disabled for host in hosts.values())

    squad = next(iter(panel.squads.values()))
    assert len(squad.inbound_uuids) == 5
    assert set(squad.inbound_uuids) == {
        inbound.uuid
        for profile in panel.profiles.values()
        for inbound in profile.inbounds
    }
    assert cluster.workloads[0].reality_keys["srv-exit-a"].private_key == "private-exit-a"
    assert cluster.workloads[1].reality_keys["srv-exit-b"].private_key == "private-exit-b"
    assert cluster.workloads[0].config_profile_uuid != cluster.workloads[1].config_profile_uuid

    mutation_counts = panel.calls.copy()
    second = execute_resource_plan(
        plan,
        cluster,
        drivers,
        persist=lambda _state: None,
    )

    assert second.all_succeeded
    assert not second.changed
    assert key_calls == ["srv-exit-a", "srv-exit-b"]
    assert panel.calls == mutation_counts


def test_unbound_mismatched_profile_name_collision_is_not_adopted() -> None:
    plan = compile_topology(_intent())
    profile_resource = next(
        resource
        for resource in plan.resources
        if isinstance(resource.payload, ConfigProfilePayload)
        and resource.payload.workload_id == "exit-a"
    )
    action = next(
        action
        for action in build_resource_actions(plan, generation=1)
        if action.resource.logical_id == profile_resource.logical_id
    )
    panel = StatefulPanel()
    panel.create_config_profile(profile_resource.payload.name, {"inbounds": []})
    cluster = ClusterConfig()
    workloads = WorkloadStateManager(
        cluster,
        persist=lambda _state: None,
        key_factory=_keys,
    )
    context = RemnawaveDriverContext(
        panel=cast(MeridianPanel, panel),
        plan=plan,
        cluster=cluster,
        workloads=workloads,
        server_addresses={"srv-exit-a": "198.51.100.20"},
    )
    driver = build_remnawave_drivers(context)["config_profile"]

    observed = driver.observe(action, None)
    assert observed.observed_hash != action.expected_hash

    with pytest.raises(ResourceReconcileError, match="Unmanaged"):
        driver.apply(action, None)
