"""Setup runtime wiring from reviewed intent through resumable apply."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import cast, get_args
from unittest.mock import MagicMock

import pytest

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.compiler.models import RealmHopPayload, ResourceKind, ResourcePlan
from meridian.core.errors import LocalStateError
from meridian.core.setup import SetupDraft
from meridian.core.topology import (
    AccessIntent,
    ControlPlaneIntent,
    ExitIntent,
    ProtocolPathIntent,
    SetupIntent,
    TransparentRelayIntent,
)
from meridian.reconciler.contract_drivers import ContractResourceDriver
from meridian.reconciler.resources import ResourceDrivers
from meridian.remnawave import MeridianPanel
from meridian.servers import ServerEntry, ServerRegistry
from meridian.setup.runtime import SetupRuntime
from meridian.ssh import ServerConnection


class _Panel:
    def __init__(self) -> None:
        self.closed = 0

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        self.closed += 1

    def list_nodes(self) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                name="Meridian v4 exit-a",
                is_disabled=False,
                is_connected=True,
            )
        ]

    def get_node(self, _uuid: str) -> SimpleNamespace:
        return SimpleNamespace(
            is_disabled=False,
            is_connected=True,
            active_config_profile_uuid="profile:exit-a",
        )

    def get_user(self, username: str) -> SimpleNamespace:
        return SimpleNamespace(
            username=username,
            short_uuid=f"short-{username}",
        )

    def fetch_subscription(
        self,
        short_uuid: str,
        *,
        client_type: str = "",
    ) -> SimpleNamespace:
        return SimpleNamespace(
            url=f"https://panel.example/api/sub/{short_uuid}/{client_type}",
            content='{"outbounds": [{"tag": "MERIDIAN_PROXY_1", "protocol": "vless"}]}',
        )


def _intent() -> SetupIntent:
    return SetupIntent(
        control=ControlPlaneIntent(server_ref="srv-control"),
        exits=[
            ExitIntent(
                id="exit-a",
                server_ref="srv-exit",
                paths=[
                    ProtocolPathIntent(
                        id="exit-a-reality",
                        protocol="reality",
                        reality_sni="www.microsoft.com",
                    )
                ],
            )
        ],
        default_egress_ref="exit-a",
        access=AccessIntent(users=["default"]),
    )


def _drivers(plan: ResourcePlan) -> ResourceDrivers:
    driver = ContractResourceDriver()
    return cast(
        ResourceDrivers,
        {kind: driver for kind in get_args(ResourceKind)},
    )


def _add_servers(registry: ServerRegistry, *, relay: bool = False) -> None:
    registry.add(
        ServerEntry(
            host="198.51.100.10",
            name="control",
            id="srv-control",
            auth_state="validated",
        )
    )
    registry.add(
        ServerEntry(
            host="198.51.100.20",
            name="exit",
            id="srv-exit",
            auth_state="validated",
        )
    )
    if relay:
        registry.add(
            ServerEntry(
                host="198.51.100.30",
                name="relay",
                id="srv-relay",
                auth_state="validated",
            )
        )


def test_review_preserves_nondefault_relay_listener_through_setup_draft(tmp_path) -> None:
    base = _intent()
    intent = SetupIntent(
        control=base.control,
        exits=base.exits,
        transparent_relays=[
            TransparentRelayIntent(
                id="relay-a",
                hop_server_refs=["srv-relay"],
                exit_ref="exit-a",
                protocol_path_ref="exit-a-reality",
                listen_port=8443,
            )
        ],
        default_egress_ref=base.default_egress_ref,
        access=base.access,
        delivery=base.delivery,
    )
    seeded = SetupDraft.from_intent(intent)
    draft = SetupDraft.model_validate_json(seeded.model_dump_json(by_alias=True))

    registry = ServerRegistry(tmp_path / "servers.json")
    _add_servers(registry, relay=True)
    review = SetupRuntime(registry).review(draft)
    relay_hop = next(
        resource.payload for resource in review.plan.resources if isinstance(resource.payload, RealmHopPayload)
    )

    assert draft.to_intent() == intent
    assert review.intent.transparent_relays[0].listen_port == 8443
    assert relay_hop.listen_port == 8443


def test_review_ignores_selected_servers_that_have_no_topology_role(tmp_path) -> None:
    registry = ServerRegistry(tmp_path / "servers.json")
    _add_servers(registry)
    registry.add(
        ServerEntry(
            host="198.51.100.30",
            name="unused",
            id="srv-unused",
            auth_state="validated",
        )
    )
    draft = SetupDraft.from_intent(_intent()).model_copy(
        update={"server_refs": ["srv-control", "srv-exit", "srv-unused"]}
    )

    review = SetupRuntime(registry).review(draft)

    assert set(review.plan.deployment_contract.server_targets) == {"srv-control", "srv-exit"}


def test_reviewed_apply_resumes_to_a_no_op_and_verifies_canonical_subscription(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = ServerRegistry(tmp_path / "servers.json")
    _add_servers(registry)
    cluster = ClusterConfig(
        panel=PanelConfig(
            url="https://panel.example",
            api_token="token",
            server_ip="198.51.100.10",
        )
    )
    panel = _Panel()
    connection = MagicMock(spec=ServerConnection)
    remote_files: dict[str, str] = {}
    connection.run.return_value = SimpleNamespace(
        returncode=0,
        stdout=("LISTEN 0 4096 127.0.0.1:8443 0.0.0.0:*\nLISTEN 0 4096 0.0.0.0:443 0.0.0.0:*\n"),
    )
    connection.get_text.side_effect = lambda path, **_kwargs: SimpleNamespace(
        returncode=0 if path in remote_files else 1,
        stdout=remote_files.get(path, ""),
    )

    def put_text(path: str, content: str, **_kwargs):
        remote_files[path] = content
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    connection.put_text.side_effect = put_text
    persists: list[str] = []

    def repair_control(_payload, _plan, state, entry, _connection) -> None:
        state.panel.server_ip = entry.host
        state.panel.ssh_user = entry.user
        state.panel.ssh_port = entry.port

    bootstrap = MagicMock(side_effect=repair_control)
    runtime = SetupRuntime(
        registry,
        cluster_loader=lambda: cluster,
        persist=lambda state: persists.append(state.active_plan_hash),
        panel_factory=lambda _url, _token: cast(
            MeridianPanel,
            panel,
        ),
        connection_builder=lambda _entry: connection,
        control_bootstrap=bootstrap,
    )
    monkeypatch.setattr(
        "meridian.setup.runtime.build_remnawave_drivers",
        lambda context: _drivers(context.plan),
    )
    monkeypatch.setattr(
        "meridian.setup.runtime.build_server_drivers",
        lambda context: _drivers(context.plan),
    )
    draft = SetupDraft.from_intent(_intent())
    plan_hash = runtime.review(draft).plan.plan_hash
    draft = draft.model_copy(
        update={
            "review_hash": plan_hash,
            "current_stage": "apply",
            "completed_stages": [
                "servers",
                "roles",
                "paths",
                "routing",
                "access",
                "review",
            ],
        }
    )

    first = runtime.apply(draft)
    second = runtime.apply(draft)
    before_inspection = copy.deepcopy({key: value for key, value in vars(cluster).items() if key != "_lock"})
    persist_count = len(persists)
    inspection = runtime.inspect_intent(_intent())

    assert first.all_succeeded, [
        (
            item.action.resource.logical_id,
            item.status,
            item.error,
        )
        for item in first.results
        if not item.succeeded
    ]
    assert first.changed
    assert second.all_succeeded and not second.changed
    assert inspection.converged
    assert inspection.drifted == []
    assert {key: value for key, value in vars(cluster).items() if key != "_lock"} == before_inspection
    assert len(persists) == persist_count
    assert cluster.active_plan_hash == plan_hash
    assert cluster.topology_intent == _intent()
    bootstrap.assert_called_once()
    applied = draft.model_copy(update={"applied_plan_hash": plan_hash})
    verification = runtime.verify(applied)
    assert verification.node_count == 1
    assert verification.subscription_urls == {"default": "https://panel.example/api/sub/short-default/xray-json"}
    assert persists

    cluster.panel.server_ip = "198.51.100.99"
    repaired = runtime.apply(draft)
    assert repaired.all_succeeded and repaired.changed
    assert cluster.panel.server_ip == "198.51.100.10"
    assert bootstrap.call_count == 2


def test_apply_rejects_unreviewed_hash_before_remote_work(
    tmp_path,
) -> None:
    registry = ServerRegistry(tmp_path / "servers.json")
    _add_servers(registry)
    runtime = SetupRuntime(registry)
    draft = SetupDraft.from_intent(_intent()).model_copy(update={"review_hash": "stale"})

    with pytest.raises(LocalStateError, match="no longer matches"):
        runtime.apply(draft)


def test_review_hash_changes_when_a_saved_server_target_changes(tmp_path) -> None:
    registry = ServerRegistry(tmp_path / "servers.json")
    _add_servers(registry)
    runtime = SetupRuntime(registry)
    draft = SetupDraft.from_intent(_intent())
    original = runtime.review(draft).plan

    registry.add(
        ServerEntry(
            host="198.51.100.20",
            user="ubuntu",
            port=2222,
            name="exit",
            id="srv-exit",
            auth_state="validated",
        )
    )
    changed = runtime.review(draft).plan

    assert changed.plan_hash != original.plan_hash
    assert changed.deployment_contract.server_targets["srv-exit"].user == "ubuntu"
    assert changed.deployment_contract.server_targets["srv-exit"].port == 2222
