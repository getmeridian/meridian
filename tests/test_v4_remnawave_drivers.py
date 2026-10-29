"""Stateful two-exit tests for V4 Remnawave resource drivers."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace
from typing import cast
from uuid import UUID

import pytest

from meridian.cluster import ClusterConfig, ManagedResourceBinding, RealityKeyBinding, WorkloadBinding
from meridian.compiler import compile_topology
from meridian.compiler.models import (
    NON_XRAY_JSON_TYPES,
    SUBSCRIPTION_UPDATE_INTERVAL_HOURS,
    ConfigProfilePayload,
    ResourcePlan,
)
from meridian.compiler.routing import edge_outbound_tag
from meridian.core.topology import (
    AccessIntent,
    ControlPlaneIntent,
    EgressPoolIntent,
    ExitIntent,
    OrderedRouteIntent,
    ProtocolPathIntent,
    RoutingGatewayIntent,
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
from meridian.reconciler.workloads import WorkloadStateError, WorkloadStateManager
from meridian.remnawave import (
    ConfigProfile,
    ExternalSquad,
    Host,
    Inbound,
    InternalSquad,
    MeridianPanel,
    Node,
    NodeCredentials,
    SubscriptionSettings,
    SubscriptionTemplate,
    User,
)
from meridian.xray_workload import WorkloadConfigError

_REMNAWAVE_DISPLAY_NAME_RE = re.compile(r"^[A-Za-z0-9_ -]+$")
_REMNAWAVE_USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _assert_short_display_name(name: str, *, min_length: int = 2) -> None:
    assert min_length <= len(name) <= 30
    assert _REMNAWAVE_DISPLAY_NAME_RE.fullmatch(name)


def _assert_username(username: str) -> None:
    assert 3 <= len(username) <= 36
    assert _REMNAWAVE_USERNAME_RE.fullmatch(username)


class StatefulPanel:
    def __init__(self) -> None:
        self.profiles: dict[str, ConfigProfile] = {}
        self.nodes: dict[str, Node] = {}
        self.hosts: dict[str, Host] = {}
        self.squads: dict[str, InternalSquad] = {}
        self.external_squads: dict[str, ExternalSquad] = {}
        self.templates: dict[str, SubscriptionTemplate] = {}
        self.users: dict[str, User] = {}
        self.calls: Counter[str] = Counter()
        self._next_id = 1
        self._next_user_id = 1
        self.subscription_settings = SubscriptionSettings(
            uuid=self._uuid(),
            custom_response_headers={"profile-update-interval": "12"},
        )

    def _user_id(self) -> str:
        value = str(self._next_user_id)
        self._next_user_id += 1
        return value

    def _uuid(self) -> str:
        value = str(UUID(int=self._next_id))
        self._next_id += 1
        return value

    def create_config_profile(self, name: str, config: dict) -> ConfigProfile:
        _assert_short_display_name(name)
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
        if name is not None:
            _assert_short_display_name(name)
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
        _assert_short_display_name(name, min_length=3)
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
        _assert_short_display_name(name, min_length=3)
        self.calls["rename_node"] += 1
        self.nodes[uuid].name = name

    def update_node_country(self, uuid: str, country_code: str) -> None:
        self.calls["country_node"] += 1
        self.nodes[uuid].country_code = country_code

    def update_node_address(self, uuid: str, address: str, port: int) -> None:
        self.calls["move_node"] += 1
        self.nodes[uuid].address = address
        self.nodes[uuid].port = port

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
        host.view_position = self.hosts[uuid].view_position  # the panel keeps positions on update
        self.hosts[uuid] = host
        return host

    def set_host_positions(self, positions: dict[str, int]) -> None:
        self.calls["position_host"] += 1
        for uuid, position in positions.items():
            self.hosts[uuid].view_position = position

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
            tags=list(cast(list[str], values["tags"])),
            is_hidden=bool(values["is_hidden"]),
            xray_json_template_uuid=str(values["xray_json_template_uuid"]),
            exclude_from_subscription_types=list(cast(list[str], values["exclude_from_subscription_types"])),
        )

    def create_internal_squad(self, name: str, inbound_uuids: list[str]) -> InternalSquad:
        _assert_short_display_name(name)
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
        if name is not None:
            _assert_short_display_name(name)
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

    def create_subscription_template(
        self,
        name: str,
        template_type: str,
    ) -> SubscriptionTemplate:
        assert 2 <= len(name) <= 255
        assert _REMNAWAVE_DISPLAY_NAME_RE.fullmatch(name)
        self.calls["create_template"] += 1
        template = SubscriptionTemplate(
            uuid=self._uuid(),
            name=name,
            template_type=template_type,
        )
        self.templates[template.uuid] = template
        return template

    def update_subscription_template(
        self,
        uuid: str,
        *,
        name: str | None = None,
        template_json: dict | None = None,
        encoded_template_yaml: str | None = None,
    ) -> SubscriptionTemplate:
        if name is not None:
            assert 2 <= len(name) <= 255
            assert _REMNAWAVE_DISPLAY_NAME_RE.fullmatch(name)
        self.calls["update_template"] += 1
        template = self.templates[uuid]
        if name is not None:
            template.name = name
        if template_json is not None:
            template.template_json = template_json
        if encoded_template_yaml is not None:
            template.encoded_template_yaml = encoded_template_yaml
        return template

    def get_subscription_template(self, uuid: str) -> SubscriptionTemplate | None:
        return self.templates.get(uuid)

    def list_subscription_templates(self) -> list[SubscriptionTemplate]:
        # Remnawave 3.4 lists templates without their content; only GET by UUID returns it.
        return [replace(template, template_json=None, encoded_template_yaml="") for template in self.templates.values()]

    def create_external_squad(self, name: str) -> ExternalSquad:
        _assert_short_display_name(name)
        self.calls["create_external_squad"] += 1
        squad = ExternalSquad(uuid=self._uuid(), name=name)
        self.external_squads[squad.uuid] = squad
        return squad

    def update_external_squad(
        self,
        uuid: str,
        *,
        name: str | None = None,
        templates: list[dict[str, str]] | None = None,
        subscription_settings: dict | None = None,
        response_headers: dict[str, str] | None = None,
    ) -> ExternalSquad:
        if name is not None:
            _assert_short_display_name(name)
        self.calls["update_external_squad"] += 1
        squad = self.external_squads[uuid]
        if name is not None:
            squad.name = name
        if templates is not None:
            squad.templates = templates
        return squad

    def get_external_squad(self, uuid: str) -> ExternalSquad | None:
        return self.external_squads.get(uuid)

    def list_external_squads(self) -> list[ExternalSquad]:
        return list(self.external_squads.values())

    def create_access_user(
        self,
        username: str,
        *,
        squad_uuids: list[str],
        external_squad_uuid: str = "",
        expire_at: str = "2099-12-31T23:59:59.000Z",
    ) -> User:
        _assert_username(username)
        self.calls["create_access_user"] += 1
        user = User(
            id=self._user_id(),
            username=username,
            vless_uuid=self._uuid(),
            status="ACTIVE",
            active_internal_squad_uuids=list(squad_uuids),
            external_squad_uuid=external_squad_uuid,
            description="Managed by Meridian access",
        )
        self.users[user.id] = user
        return user

    def update_access_user(
        self,
        user_id: str,
        *,
        squad_uuids: list[str],
        external_squad_uuid: str = "",
    ) -> User:
        self.calls["update_access_user"] += 1
        self.users[user_id].active_internal_squad_uuids = list(squad_uuids)
        self.users[user_id].external_squad_uuid = external_squad_uuid
        return self.users[user_id]

    def enable_user(self, user_id: str) -> None:
        self.calls["enable_user"] += 1
        self.users[user_id].status = "ACTIVE"

    def get_subscription_settings(self) -> SubscriptionSettings:
        return self.subscription_settings

    def update_subscription_settings(
        self,
        uuid: str,
        *,
        profile_title: str | None = None,
        profile_update_interval_hours: int | None = None,
        response_rules: list[dict] | None = None,
        serve_json_at_base_subscription: bool | None = None,
        randomize_hosts: bool | None = None,
        custom_response_headers: dict[str, str] | None = None,
    ) -> SubscriptionSettings:
        self.calls["update_subscription_settings"] += 1
        assert uuid == self.subscription_settings.uuid
        assert response_rules is None
        if profile_title is not None or profile_update_interval_hours is not None:
            # Mirror the adapter: the header record is replaced wholesale.
            headers = dict(
                custom_response_headers
                if custom_response_headers is not None
                else self.subscription_settings.custom_response_headers
            )
            if profile_title is not None:
                headers["profile-title"] = f"rwEncodeBase64:{profile_title}"
            if profile_update_interval_hours is not None:
                headers["profile-update-interval"] = str(profile_update_interval_hours)
            self.subscription_settings.custom_response_headers = headers
        if randomize_hosts is not None:
            self.subscription_settings.randomize_hosts = randomize_hosts
        if serve_json_at_base_subscription is not None:
            self.subscription_settings.serve_json_at_base_subscription = serve_json_at_base_subscription
        return self.subscription_settings

    def create_service_user(
        self,
        username: str,
        *,
        squad_uuids: list[str],
        external_squad_uuid: str = "",
        expire_at: str = "2099-12-31T23:59:59.000Z",
    ) -> User:
        _assert_username(username)
        self.calls["create_service_user"] += 1
        user_id = self._user_id()
        user = User(
            id=user_id,
            username=username,
            vless_uuid=self._uuid(),
            status="ACTIVE",
            active_internal_squad_uuids=list(squad_uuids),
            description="Managed by Meridian service routing",
        )
        self.users[user_id] = user
        return user

    def update_service_user(
        self,
        user_id: str,
        *,
        squad_uuids: list[str],
        external_squad_uuid: str = "",
    ) -> User:
        self.calls["update_service_user"] += 1
        self.users[user_id].active_internal_squad_uuids = list(squad_uuids)
        return self.users[user_id]

    def get_user_by_id(self, user_id: str) -> User | None:
        assert user_id.isdigit(), "legacy uuid identities must never reach the 3.x user route"
        return self.users.get(user_id)

    def get_user(self, username: str) -> User | None:
        return next(
            (user for user in self.users.values() if user.username == username),
            None,
        )


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
                postcondition_key(item.kind, item.target_ref, item.detail) for item in action.resource.postconditions
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


def test_missing_profile_observation_does_not_allocate_workload_secrets() -> None:
    plan = compile_topology(_intent())
    action = next(
        action
        for action in build_resource_actions(plan, generation=1)
        if isinstance(action.resource.payload, ConfigProfilePayload) and action.resource.payload.workload_id == "exit-a"
    )
    cluster = ClusterConfig()
    panel = StatefulPanel()
    key_calls: list[str] = []
    persist_calls = 0

    def persist(_state: ClusterConfig) -> None:
        nonlocal persist_calls
        persist_calls += 1

    workloads = WorkloadStateManager(
        cluster,
        persist=persist,
        key_factory=lambda server_ref: (key_calls.append(server_ref), _keys(server_ref))[1],
    )
    context = RemnawaveDriverContext(
        panel=cast(MeridianPanel, panel),
        plan=plan,
        cluster=cluster,
        workloads=workloads,
        server_addresses={"srv-exit-a": "198.51.100.20"},
    )

    observed = build_remnawave_drivers(context)["config_profile"].observe(action, None)

    assert not observed.exists
    assert key_calls == []
    assert persist_calls == 0
    assert cluster.workloads == []
    assert panel.calls["create_profile"] == 0


def test_existing_profile_with_incomplete_reality_keys_is_an_observation_error() -> None:
    plan = compile_topology(_intent())
    action = next(
        action
        for action in build_resource_actions(plan, generation=1)
        if isinstance(action.resource.payload, ConfigProfilePayload) and action.resource.payload.workload_id == "exit-a"
    )
    payload = cast(ConfigProfilePayload, action.resource.payload)
    panel = StatefulPanel()
    profile = panel.create_config_profile(payload.name, {"inbounds": []})
    cluster = ClusterConfig(
        workloads=[
            WorkloadBinding(
                id="exit-a",
                generation=1,
                config_profile_uuid=profile.uuid,
                desired_hash=action.expected_hash,
                reality_keys={
                    "srv-exit-a": RealityKeyBinding(
                        public_key="public-exit-a",
                        private_key="",
                        short_id="short-exit-a",
                    )
                },
            )
        ]
    )
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
        server_addresses={"srv-exit-a": "198.51.100.20"},
    )
    mutation_counts = panel.calls.copy()

    with pytest.raises(WorkloadConfigError, match="Reality key material is incomplete"):
        build_remnawave_drivers(context)["config_profile"].observe(action, None)

    assert key_calls == []
    assert panel.calls == mutation_counts


def test_moved_exit_repoints_its_owned_node_instead_of_refusing() -> None:
    plan = compile_topology(_intent())
    cluster = ClusterConfig()
    panel = StatefulPanel()
    workloads = WorkloadStateManager(cluster, persist=lambda _state: None, key_factory=_keys)
    addresses = {"srv-control": "198.51.100.10", "srv-exit-a": "198.51.100.20", "srv-exit-b": "198.51.100.30"}

    def run(server_addresses: dict[str, str]) -> None:
        context = RemnawaveDriverContext(
            panel=cast(MeridianPanel, panel),
            plan=plan,
            cluster=cluster,
            workloads=workloads,
            server_addresses=server_addresses,
        )
        fallback = AlreadyConvergedDriver()
        drivers = {kind: fallback for kind in {resource.payload.kind for resource in plan.resources}}
        drivers.update(build_remnawave_drivers(context))
        result = execute_resource_plan(plan, cluster, drivers, persist=lambda _state: None)
        assert result.all_succeeded, result.failure_summary

    run(addresses)
    node_uuid = next(node.uuid for node in panel.nodes.values() if node.name == "Meridian v4 exit-a")
    cluster.action_checkpoints = {}  # a later apply re-observes every action
    run({**addresses, "srv-exit-a": "198.51.100.40"})

    assert panel.calls["move_node"] == 1
    assert panel.nodes[node_uuid].address == "198.51.100.40"
    assert len(panel.nodes) == 2


def test_two_exits_reconcile_distinct_profiles_nodes_hosts_and_squad() -> None:
    plan = compile_topology(_intent())
    cluster = ClusterConfig()
    panel = StatefulPanel()
    panel.subscription_settings.response_rules = [{"name": "unmanaged"}]
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
    exit_a_profile = profiles["Meridian v4 exit-a"]
    exit_b_profile = profiles["Meridian v4 exit-b"]
    assert [item.tag for item in exit_a_profile.inbounds] == [
        "meridian-exit-a-reality",
        "meridian-exit-a-xhttp",
    ]
    assert [item.tag for item in exit_b_profile.inbounds] == [
        "meridian-exit-b-hysteria2",
        "meridian-exit-b-reality",
        "meridian-exit-b-wss",
    ]
    assert exit_a_profile.config["inbounds"][0]["streamSettings"]["realitySettings"]["privateKey"] == "private-exit-a"
    assert exit_b_profile.config["inbounds"][1]["streamSettings"]["realitySettings"]["privateKey"] == "private-exit-b"
    assert [item["tag"] for item in exit_a_profile.config["outbounds"]] == ["direct", "block"]
    assert [item["tag"] for item in exit_b_profile.config["outbounds"]] == [
        "warp",
        "direct",
        "block",
    ]

    assert len(panel.nodes) == 2
    nodes = {node.name: node for node in panel.nodes.values()}
    assert nodes["Meridian v4 exit-a"].active_config_profile_uuid == exit_a_profile.uuid
    assert nodes["Meridian v4 exit-b"].active_config_profile_uuid == exit_b_profile.uuid
    assert set(nodes["Meridian v4 exit-a"].active_inbound_uuids) == {item.uuid for item in exit_a_profile.inbounds}
    assert set(nodes["Meridian v4 exit-b"].active_inbound_uuids) == {item.uuid for item in exit_b_profile.inbounds}

    assert len(panel.hosts) == 18
    hosts = list(panel.hosts.values())
    direct_hosts = [host for host in hosts if not host.is_hidden and not host.xray_json_template_uuid]
    hidden_edges = [host for host in hosts if host.is_hidden]
    virtual_hosts = [host for host in hosts if host.xray_json_template_uuid]
    # Reality and XHTTP entries are published with a Chrome and a Firefox copy.
    assert len(direct_hosts) == 8
    assert len(hidden_edges) == 8
    # One balancer per fingerprint: Chrome entries and their Firefox copies never mix.
    assert sorted(host.remark for host in virtual_hosts) == ["Automatic", "Automatic · Firefox"]
    assert any(host.path == "/xhttp-a" for host in direct_hosts)
    assert any(host.path == "/ws-b" for host in direct_hosts)
    assert any(host.alpn == "h3" for host in direct_hosts)
    assert all(host.fingerprint in ({""} if host.alpn == "h3" else {"chrome", "firefox"}) for host in direct_hosts)
    copies = [host for host in direct_hosts if host.fingerprint == "firefox"]
    assert all(host.tags == ["MERIDIAN_V4_ALT_FINGERPRINT", "MERIDIAN_V4_VISIBLE"] for host in copies)
    assert all(host.tags == ["MERIDIAN_V4_VISIBLE"] for host in direct_hosts if host not in copies)
    assert all("SINGBOX" in host.exclude_from_subscription_types for host in copies)
    edge_tags = sorted(tag for host in hidden_edges for tag in host.tags)
    assert Counter(edge_tags) == {"MERIDIAN_V4_XRAY_ALT_EDGE": len(copies), "MERIDIAN_V4_XRAY_EDGE": 8 - len(copies)}
    assert all(set(host.exclude_from_subscription_types) == set(NON_XRAY_JSON_TYPES) for host in hidden_edges)
    # Each owned Host carries its compiled subscription position.
    assert sorted(host.view_position for host in hosts) == list(range(len(hosts)))
    assert all(host.tags == ["MERIDIAN_V4_XRAY_VIRTUAL"] for host in virtual_hosts)
    assert all(not host.is_disabled for host in hosts)
    assert all(len(host.remark) <= 100 for host in hosts)

    squad = next(iter(panel.squads.values()))
    assert len(squad.inbound_uuids) == 5
    assert set(squad.inbound_uuids) == {
        inbound.uuid for profile in panel.profiles.values() for inbound in profile.inbounds
    }
    assert cluster.workloads[0].reality_keys["srv-exit-a"].private_key == "private-exit-a"
    assert cluster.workloads[1].reality_keys["srv-exit-b"].private_key == "private-exit-b"
    assert cluster.workloads[0].config_profile_uuid != cluster.workloads[1].config_profile_uuid
    assert panel.subscription_settings.response_rules == [{"name": "unmanaged"}]

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


def test_gateway_reconciles_service_edges_ordered_routes_and_fail_closed_pool() -> None:
    base = _intent()
    intent = SetupIntent(
        control=base.control,
        exits=base.exits,
        routing_gateways=[
            RoutingGatewayIntent(
                id="gateway-a",
                server_ref="srv-gateway",
                bridge_path_ref="reality-a",
            )
        ],
        egress_pools=[
            EgressPoolIntent(
                id="primary",
                exit_refs=["exit-a", "exit-b"],
                strategy="least_ping",
            )
        ],
        routes=[
            OrderedRouteIntent(
                id="regional",
                priority=10,
                match="country",
                match_values=["DE"],
                target_ref="exit-a",
                source_gateway_ref="gateway-a",
            )
        ],
        default_egress_ref="primary",
        access=base.access,
    )
    plan = compile_topology(intent)
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
            "srv-gateway": "198.51.100.40",
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
    assert sorted(key_calls) == ["srv-exit-a", "srv-exit-b", "srv-gateway"]
    service_users = [user for user in panel.users.values() if user.description == "Managed by Meridian service routing"]
    assert len(service_users) == 2
    assert any(user.description == "Managed by Meridian access" for user in panel.users.values())
    assert len(panel.profiles) == 3
    profiles = {profile.name: profile for profile in panel.profiles.values()}
    gateway = profiles["Meridian v4 gateway-a"]
    service_outbound_tags = [
        edge_outbound_tag("gateway-a", "exit-a"),
        edge_outbound_tag("gateway-a", "exit-b"),
    ]
    assert [outbound["tag"] for outbound in gateway.config["outbounds"][:2]] == service_outbound_tags
    assert gateway.config["routing"]["balancers"][0]["fallbackTag"] == "block"
    assert gateway.config["routing"]["rules"][-1]["balancerTag"] == ("meridian-pool-gateway-a-primary")
    assert gateway.config["observatory"]["subjectSelector"] == service_outbound_tags
    assert {
        inbound.tag for profile in profiles.values() for inbound in profile.inbounds if "bridge" in inbound.tag
    } == {
        "meridian-exit-a-bridge-gateway-a",
        "meridian-exit-b-bridge-gateway-a",
    }

    # Operator-owned fields: a disabled (or limited/expired) user and an edited
    # description survive apply, and apply still converges.
    access_user = next(user for user in panel.users.values() if user.description == "Managed by Meridian access")
    access_user.status = "DISABLED"
    access_user.description = "Grandma's tablet"
    # `client set` limits and expiry are the operator's too.
    access_user.traffic_limit_bytes = 50 * 1024**3
    access_user.traffic_limit_strategy = "MONTH"
    access_user.expire_at = "2031-12-31T23:59:59.000Z"
    restored = execute_resource_plan(
        plan,
        cluster,
        drivers,
        persist=lambda _state: None,
    )
    assert restored.all_succeeded
    assert not restored.changed
    assert access_user.status == "DISABLED"
    assert access_user.description == "Grandma's tablet"
    assert (access_user.traffic_limit_bytes, access_user.traffic_limit_strategy, access_user.expire_at) == (
        50 * 1024**3,
        "MONTH",
        "2031-12-31T23:59:59.000Z",
    )
    assert panel.calls["enable_user"] == 0

    mutation_counts = panel.calls.copy()
    second = execute_resource_plan(
        plan,
        cluster,
        drivers,
        persist=lambda _state: None,
    )
    assert second.all_succeeded
    assert not second.changed
    assert panel.calls == mutation_counts

    # Remnawave 2.x bindings stored user uuids; 3.x users are addressed by id.
    user_kinds = {"access_user", "service_user"}
    user_bindings = [binding for binding in cluster.managed_bindings.values() if binding.resource_kind in user_kinds]
    assert len(user_bindings) == 3
    for index, binding in enumerate(user_bindings):
        binding.remote_id = str(UUID(int=10_000 + index))
    migrated = execute_resource_plan(
        plan,
        cluster,
        drivers,
        persist=lambda _state: None,
    )
    assert migrated.all_succeeded
    assert panel.calls == mutation_counts
    rebound = [binding for binding in cluster.managed_bindings.values() if binding.resource_kind in user_kinds]
    assert sorted(binding.remote_id for binding in rebound) == sorted(panel.users)


def test_unbound_mismatched_profile_name_collision_is_not_adopted() -> None:
    plan = compile_topology(_intent())
    profile_resource = next(
        resource
        for resource in plan.resources
        if isinstance(resource.payload, ConfigProfilePayload) and resource.payload.workload_id == "exit-a"
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

    with pytest.raises(WorkloadStateError, match="Workload state exit-a@1 is missing"):
        driver.observe(action, None)

    with pytest.raises(ResourceReconcileError, match="Unmanaged"):
        driver.apply(action, None)


def test_intent_change_after_first_apply_converges_with_the_same_keys() -> None:
    first_plan = compile_topology(_intent())
    cluster = ClusterConfig()
    panel = StatefulPanel()
    key_calls: list[str] = []
    workloads = WorkloadStateManager(
        cluster,
        persist=lambda _state: None,
        key_factory=lambda server_ref: (key_calls.append(server_ref), _keys(server_ref))[1],
    )
    addresses = {
        "srv-control": "198.51.100.10",
        "srv-exit-a": "198.51.100.20",
        "srv-exit-b": "198.51.100.30",
    }

    def drivers_for(plan: ResourcePlan) -> dict[str, object]:
        context = RemnawaveDriverContext(
            panel=cast(MeridianPanel, panel),
            plan=plan,
            cluster=cluster,
            workloads=workloads,
            server_addresses=addresses,
        )
        fallback = AlreadyConvergedDriver()
        drivers: dict[str, object] = {kind: fallback for kind in {resource.payload.kind for resource in plan.resources}}
        drivers.update(build_remnawave_drivers(context))
        return drivers

    assert execute_resource_plan(
        first_plan, cluster, drivers_for(first_plan), persist=lambda _state: None
    ).all_succeeded

    grown = _intent().model_copy(update={"access": AccessIntent(users=["default", "zed"])})
    second_plan = compile_topology(grown)
    assert second_plan.plan_hash != first_plan.plan_hash

    result = execute_resource_plan(second_plan, cluster, drivers_for(second_plan), persist=lambda _state: None)

    assert result.all_succeeded, [item.error for item in result.results if item.error]
    assert cluster.active_generation == 2
    assert sorted(key_calls) == ["srv-exit-a", "srv-exit-b"]
    # A new generation finds unchanged Templates by name and must not rewrite them.
    template_statuses = {
        item.status for item in result.results if item.action.resource.payload.kind == "subscription_template"
    }
    assert template_statuses == {"converged"}
    second_gen = {item.id: item for item in cluster.workloads if item.generation == 2}
    assert second_gen["exit-a"].reality_keys["srv-exit-a"].private_key == "private-exit-a"
    assert second_gen["exit-b"].reality_keys["srv-exit-b"].private_key == "private-exit-b"


def test_adding_an_exit_renames_existing_hosts_in_place_instead_of_twinning_them() -> None:
    # A second exit prefixes every remark with its exit ID; looking Hosts up by the
    # new remark alone created twins and stranded the first generation's Hosts.
    single = _intent().model_copy(update={"exits": [_intent().exits[0]]})
    cluster = ClusterConfig()
    panel = StatefulPanel()
    workloads = WorkloadStateManager(cluster, persist=lambda _state: None, key_factory=_keys)
    addresses = {"srv-control": "198.51.100.10", "srv-exit-a": "198.51.100.20", "srv-exit-b": "198.51.100.30"}

    def run(plan: ResourcePlan) -> None:
        context = RemnawaveDriverContext(
            panel=cast(MeridianPanel, panel),
            plan=plan,
            cluster=cluster,
            workloads=workloads,
            server_addresses=addresses,
        )
        fallback = AlreadyConvergedDriver()
        drivers: dict[str, object] = {kind: fallback for kind in {resource.payload.kind for resource in plan.resources}}
        drivers.update(build_remnawave_drivers(context))
        result = execute_resource_plan(plan, cluster, drivers, persist=lambda _state: None)
        assert result.all_succeeded, [item.error for item in result.results if item.error]

    run(compile_topology(single))
    first = {binding.logical_id: binding.remote_id for binding in cluster.managed_bindings.values() if binding.active}
    first_remarks = {host.uuid: host.remark for host in panel.hosts.values()}

    grown = compile_topology(_intent())
    run(grown)

    host_count = sum(1 for resource in grown.resources if resource.payload.kind == "host")
    assert len(panel.hosts) == host_count
    renamed = [
        logical_id
        for logical_id, remote_id in first.items()
        if logical_id.startswith("host:exit-a:") and panel.hosts[remote_id].remark != first_remarks[remote_id]
    ]
    assert renamed
    assert all(panel.hosts[first[logical_id]].remark.startswith("exit-a · ") for logical_id in renamed)


def test_node_country_follows_the_exit_region_and_otherwise_stays_the_operators() -> None:
    base = _intent()
    intent = base.model_copy(update={"exits": [base.exits[0].model_copy(update={"region": "DE"}), base.exits[1]]})
    plan = compile_topology(intent)
    cluster = ClusterConfig()
    panel = StatefulPanel()
    workloads = WorkloadStateManager(cluster, persist=lambda _state: None, key_factory=_keys)
    context = RemnawaveDriverContext(
        panel=cast(MeridianPanel, panel),
        plan=plan,
        cluster=cluster,
        workloads=workloads,
        server_addresses={"srv-control": "198.51.100.10", "srv-exit-a": "198.51.100.20", "srv-exit-b": "198.51.100.30"},
    )
    fallback = AlreadyConvergedDriver()
    drivers: dict[str, object] = {kind: fallback for kind in {resource.payload.kind for resource in plan.resources}}
    drivers.update(build_remnawave_drivers(context))

    assert execute_resource_plan(plan, cluster, drivers, persist=lambda _state: None).all_succeeded
    nodes = {node.name: node for node in panel.nodes.values()}
    assert nodes["Meridian v4 exit-a"].country_code == "DE"
    assert nodes["Meridian v4 exit-b"].country_code == "XX"

    nodes["Meridian v4 exit-a"].country_code = "FI"  # drift on a managed country is repaired
    nodes["Meridian v4 exit-b"].country_code = "SE"  # an operator's choice on an exit without a region stays
    second = execute_resource_plan(plan, cluster, drivers, persist=lambda _state: None)

    assert second.all_succeeded
    assert nodes["Meridian v4 exit-a"].country_code == "DE"
    assert nodes["Meridian v4 exit-b"].country_code == "SE"
    assert panel.calls["country_node"] == 1


@pytest.mark.parametrize(
    ("panel_url", "expected"),
    [
        ("https://198.51.100.10", "https://198.51.100.10/page-path/pwa/support.html#{{SHORT_UUID}}"),
        ("", None),
    ],
    ids=["support-page", "no-page-path-drops-placeholder"],
)
def test_seeded_support_placeholder_becomes_the_support_page_and_apps_get_xray_json(
    panel_url: str, expected: str | None
) -> None:
    plan = compile_topology(_intent())
    cluster = ClusterConfig()
    cluster.panel.url = panel_url
    cluster.panel.sub_path = "page-path"
    panel = StatefulPanel()
    panel.subscription_settings.custom_response_headers["support-url"] = "https://dummy.docs.rw"
    context = RemnawaveDriverContext(
        panel=cast(MeridianPanel, panel),
        plan=plan,
        cluster=cluster,
        workloads=WorkloadStateManager(cluster, persist=lambda _state: None, key_factory=_keys),
        server_addresses={"srv-control": "198.51.100.10", "srv-exit-a": "198.51.100.20", "srv-exit-b": "198.51.100.30"},
    )
    fallback = AlreadyConvergedDriver()
    drivers: dict[str, object] = {kind: fallback for kind in {resource.payload.kind for resource in plan.resources}}
    drivers.update(build_remnawave_drivers(context))

    assert execute_resource_plan(plan, cluster, drivers, persist=lambda _state: None).all_succeeded
    assert panel.subscription_settings.custom_response_headers.get("support-url") == expected
    assert panel.subscription_settings.serve_json_at_base_subscription is True
    assert execute_resource_plan(plan, cluster, drivers, persist=lambda _state: None).all_succeeded
    assert panel.calls["update_subscription_settings"] == 1


def test_every_subscription_carries_the_managed_refresh_interval_and_keeps_operator_headers() -> None:
    plan = compile_topology(_intent())
    cluster = ClusterConfig()
    panel = StatefulPanel()
    panel.subscription_settings.custom_response_headers["support-url"] = "https://support.example.com"
    workloads = WorkloadStateManager(cluster, persist=lambda _state: None, key_factory=_keys)
    context = RemnawaveDriverContext(
        panel=cast(MeridianPanel, panel),
        plan=plan,
        cluster=cluster,
        workloads=workloads,
        server_addresses={"srv-control": "198.51.100.10", "srv-exit-a": "198.51.100.20", "srv-exit-b": "198.51.100.30"},
    )
    fallback = AlreadyConvergedDriver()
    drivers: dict[str, object] = {kind: fallback for kind in {resource.payload.kind for resource in plan.resources}}
    drivers.update(build_remnawave_drivers(context))

    assert execute_resource_plan(plan, cluster, drivers, persist=lambda _state: None).all_succeeded
    headers = panel.subscription_settings.custom_response_headers
    assert headers["profile-update-interval"] == str(SUBSCRIPTION_UPDATE_INTERVAL_HOURS)
    assert headers["support-url"] == "https://support.example.com"
    assert panel.calls["update_subscription_settings"] == 1

    # Converged settings stay untouched; an edited interval is drift the next apply repairs.
    assert execute_resource_plan(plan, cluster, drivers, persist=lambda _state: None).all_succeeded
    assert panel.calls["update_subscription_settings"] == 1
    headers["profile-update-interval"] = "48"
    assert execute_resource_plan(plan, cluster, drivers, persist=lambda _state: None).all_succeeded
    assert panel.subscription_settings.custom_response_headers["profile-update-interval"] == str(
        SUBSCRIPTION_UPDATE_INTERVAL_HOURS
    )
    assert panel.subscription_settings.custom_response_headers["support-url"] == "https://support.example.com"
    assert panel.calls["update_subscription_settings"] == 2
