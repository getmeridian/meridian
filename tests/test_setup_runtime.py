"""Setup runtime wiring from reviewed intent through resumable apply."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import cast, get_args
from unittest.mock import MagicMock

import pytest

from meridian.cluster import ClusterConfig, ManagedResourceBinding, PanelConfig
from meridian.compiler.models import ControlPlaneRuntimePayload, RealmHopPayload, ResourceKind, ResourcePlan
from meridian.core.cancellation import OperationCancelledError
from meridian.core.defaults import DEFAULT_SNI
from meridian.core.errors import LocalStateError, ProvisioningError
from meridian.core.setup import SetupDraft
from meridian.core.topology import (
    ExitIntent,
    ProtocolPathIntent,
    SetupIntent,
    TransparentRelayIntent,
)
from meridian.reconciler.fence import FleetFenceError
from meridian.reconciler.resources import ResourceDrivers
from meridian.remnawave import MeridianPanel, RemnawaveError
from meridian.servers import ServerEntry, ServerRegistry
from meridian.setup.legacy_guard import refuse_legacy_deployments
from meridian.setup.runtime import SetupRuntime
from meridian.ssh import ServerConnection
from tests.support.cancellation import CancellationFlag
from tests.support.contract_driver import ContractResourceDriver
from tests.support.reporters import CaptureReporter
from tests.support.topology import reality_path, single_exit_intent


@pytest.fixture(autouse=True)
def _no_legacy_deployments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("meridian.setup.runtime.refuse_legacy_deployments", lambda _connections, _addresses: None)


class _Panel:
    def __init__(self) -> None:
        self.closed = 0
        self.version = "3.4.5"

    def ping(self) -> bool:
        return True

    def get_version(self) -> str:
        return self.version

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
            content=(
                '[{"inbounds": [{"tag": "SOCKS", "listen": "127.0.0.1", '
                '"port": 1080, "protocol": "socks"}], "outbounds": '
                '[{"tag": "MERIDIAN_PROXY_1", "protocol": "vless"}]}]'
            ),
        )


def _intent() -> SetupIntent:
    # The panel-built runtime below (`_Panel`, `build_remnawave_drivers`) drives a Remnawave fleet.
    intent = single_exit_intent(paths=[reality_path("exit-a-reality", "www.microsoft.com")])
    return intent.model_copy(update={"control": intent.control.model_copy(update={"backend": "remnawave"})})


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


def test_review_keeps_a_control_service_fleet_on_its_backend_when_the_draft_omits_it(tmp_path) -> None:
    registry = ServerRegistry(tmp_path / "servers.json")
    _add_servers(registry)
    service_intent = _intent().model_copy(
        update={"control": _intent().control.model_copy(update={"backend": "meridian"})}
    )
    cluster = ClusterConfig(topology_intent=service_intent)

    review = SetupRuntime(registry).review(SetupDraft.from_intent(_intent()), cluster=cluster)

    assert review.intent.control.backend == "meridian"
    assert any(resource.payload.kind == "control_service" for resource in review.plan.resources)


def test_inspect_intent_reports_unavailable_control_ssh_as_observation_error(
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
            sub_path="pages",
        )
    )
    panel = _Panel()
    connection = MagicMock(spec=ServerConnection)
    connection.get_text.return_value = SimpleNamespace(returncode=255, stdout="", stderr="SSH unavailable")
    connection.run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
    runtime = SetupRuntime(
        registry,
        cluster_loader=lambda: cluster,
        panel_factory=lambda _url, _token: cast(MeridianPanel, panel),
        connection_builder=lambda _entry: connection,
        fence_factory=lambda *_args: None,
    )
    monkeypatch.setattr(
        "meridian.setup.runtime.build_remnawave_drivers",
        lambda context: _drivers(context.plan),
    )
    monkeypatch.setattr(
        "meridian.setup.runtime.build_server_drivers",
        lambda context: _drivers(context.plan),
    )

    result = runtime.inspect_intent(_intent())

    control = next(
        inspection
        for inspection in result.inspections
        if isinstance(inspection.action.resource.payload, ControlPlaneRuntimePayload)
    )
    assert control.observation is None
    assert "control-plane attestation evidence is unavailable" in control.error
    assert "SSH transport failed" in control.error
    assert panel.closed == 1


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
            sub_path="pages",
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
        stderr="" if path in remote_files else "cat: No such file or directory",
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
        fence_factory=lambda *_args: None,
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
    assert {key: value for key, value in vars(cluster).items() if key != "_lock"} == before_inspection
    assert len(persists) == persist_count
    assert cluster.active_plan_hash == plan_hash
    assert cluster.topology_intent == _intent()
    bootstrap.assert_called_once()
    applied = draft.model_copy(update={"applied_plan_hash": plan_hash})
    verification = runtime.verify(applied)
    assert verification.node_count == 1
    assert verification.subscription_urls == {"default": "https://panel.example/pages/sub/short-default"}
    assert verification.panel_version == "3.4.5"
    assert verification.warnings == ()
    assert verification.panel_url == "https://panel.example/"
    panel.version = "4.1.0"
    newer = runtime.verify(applied)
    assert len(newer.warnings) == 1 and "newer than the tested 3.x" in newer.warnings[0]
    panel.version = "3.3.9"
    with pytest.raises(RemnawaveError, match="older than the minimum supported 3.4.0"):
        runtime.verify(applied)
    panel.version = "3.4.5"
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


def test_apply_refuses_a_server_running_meridian_3_before_persisting_intent(tmp_path, monkeypatch) -> None:
    registry = ServerRegistry(tmp_path / "servers.json")
    _add_servers(registry)
    cluster = ClusterConfig()
    persists: list[object] = []
    connection = MagicMock(spec=ServerConnection)
    connection.run.return_value = SimpleNamespace(returncode=0, stdout="3x-ui\nmeridian-legacy-check-done\n", stderr="")
    monkeypatch.setattr("meridian.setup.runtime.refuse_legacy_deployments", refuse_legacy_deployments)
    runtime = SetupRuntime(
        registry,
        cluster_loader=lambda: cluster,
        persist=persists.append,
        connection_builder=lambda _entry: connection,
        fence_factory=lambda *_args: None,
        control_bootstrap=MagicMock(side_effect=AssertionError("must not bootstrap")),
    )
    draft = SetupDraft.from_intent(_intent())
    draft = draft.model_copy(update={"review_hash": runtime.review(draft).review_hash})

    with pytest.raises(ProvisioningError, match="Meridian 3.x") as exc_info:
        runtime.apply(draft)

    assert exc_info.value.category == "user"
    assert "meridian teardown" in exc_info.value.hint
    assert persists == []
    assert cluster.topology_intent is None


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


def test_apply_intent_rejects_a_plan_changed_after_review(tmp_path) -> None:
    registry = ServerRegistry(tmp_path / "servers.json")
    _add_servers(registry)
    runtime = SetupRuntime(registry)
    intent = _intent()
    reviewed_hash = runtime.review(SetupDraft.from_intent(intent)).plan.plan_hash

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

    with pytest.raises(LocalStateError, match="changed after it was reviewed"):
        runtime.apply_intent(intent, expected_plan_hash=reviewed_hash)


def test_cancelled_apply_stops_before_remote_work_and_threads_the_token(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = ServerRegistry(tmp_path / "servers.json")
    _add_servers(registry)
    cluster = ClusterConfig()
    flag = CancellationFlag()
    flag.cancel()
    bootstrap = MagicMock()
    server_contexts: list[object] = []
    runtime = SetupRuntime(
        registry,
        cluster_loader=lambda: cluster,
        persist=lambda _state: None,
        connection_builder=lambda _entry: MagicMock(spec=ServerConnection),
        fence_factory=lambda *_args: None,
        control_bootstrap=bootstrap,
        cancellation=flag,
    )
    monkeypatch.setattr("meridian.setup.runtime.build_remnawave_drivers", lambda context: _drivers(context.plan))

    def server_drivers(context) -> ResourceDrivers:
        server_contexts.append(context.cancellation)
        return _drivers(context.plan)

    monkeypatch.setattr("meridian.setup.runtime.build_server_drivers", server_drivers)
    draft = SetupDraft.from_intent(_intent())
    draft = draft.model_copy(update={"review_hash": runtime.review(draft).plan.plan_hash})

    with pytest.raises(OperationCancelledError) as exc_info:
        runtime.apply(draft)

    assert exc_info.value.cancellation.remote_changed is False
    assert server_contexts == [flag]
    bootstrap.assert_not_called()
    assert cluster.topology_intent == _intent()
    assert cluster.pending_plan_hash == draft.review_hash
    assert cluster.active_plan_hash == ""


class _RetiringContractDriver(ContractResourceDriver):
    def __init__(self, on_retire) -> None:
        self.on_retire = on_retire

    def retire(self, binding: ManagedResourceBinding) -> None:
        self.on_retire(binding)


def _relay_binding(server_ref: str = "srv-relay") -> ManagedResourceBinding:
    return ManagedResourceBinding(
        logical_id="realm:relay-old:0",
        resource_kind="realm_hop",
        generation=1,
        remote_id="meridian-realm-0123456789abcdef",
        active=True,
        server_ref=server_ref,
    )


def _retiring_runtime(tmp_path, monkeypatch, cluster, retired, built, reporter=None) -> SetupRuntime:
    registry = ServerRegistry(tmp_path / "servers.json")
    _add_servers(registry, relay=True)
    connection = MagicMock(spec=ServerConnection)
    remote_files: dict[str, str] = {}
    connection.run.return_value = SimpleNamespace(
        returncode=0,
        stdout="LISTEN 0 4096 127.0.0.1:8443 0.0.0.0:*\nLISTEN 0 4096 0.0.0.0:443 0.0.0.0:*\n",
        stderr="",
    )
    connection.get_text.side_effect = lambda path, **_kwargs: SimpleNamespace(
        returncode=0 if path in remote_files else 1,
        stdout=remote_files.get(path, ""),
        stderr="" if path in remote_files else "cat: No such file or directory",
    )

    def put_text(path: str, content: str, **_kwargs):
        remote_files[path] = content
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    connection.put_text.side_effect = put_text

    def repair_control(_payload, _plan, state, entry, _connection) -> None:
        state.panel.server_ip = entry.host
        state.panel.ssh_user = entry.user
        state.panel.ssh_port = entry.port

    def server_drivers(context):
        drivers = _drivers(context.plan)
        drivers["realm_hop"] = _RetiringContractDriver(
            lambda binding: retired.append((binding.logical_id, context.connection(binding.server_ref)))
        )
        return drivers

    monkeypatch.setattr("meridian.setup.runtime.build_remnawave_drivers", lambda context: _drivers(context.plan))
    monkeypatch.setattr("meridian.setup.runtime.build_server_drivers", server_drivers)
    return SetupRuntime(
        registry,
        reporter=reporter,
        cluster_loader=lambda: cluster,
        persist=lambda _state: None,
        panel_factory=lambda _url, _token: cast(MeridianPanel, _Panel()),
        connection_builder=lambda entry: built.setdefault(entry.id, connection),
        fence_factory=lambda *_args: None,
        control_bootstrap=MagicMock(side_effect=repair_control),
    )


def _cluster_with_old_relay(server_ref: str = "srv-relay") -> tuple[ClusterConfig, ManagedResourceBinding]:
    binding = _relay_binding(server_ref)
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://panel.example", api_token="token", server_ip="198.51.100.10"),
        managed_bindings={"realm:relay-old:0@1": binding},
        active_generation=1,
        active_plan_hash="b" * 64,
    )
    return cluster, binding


def test_apply_reaches_servers_that_only_retiring_resources_still_reference(tmp_path, monkeypatch) -> None:
    cluster, binding = _cluster_with_old_relay()
    retired: list[tuple[str, object]] = []
    built: dict[str, object] = {}
    runtime = _retiring_runtime(tmp_path, monkeypatch, cluster, retired, built)

    review = runtime.review(SetupDraft.from_intent(_intent()))
    result = runtime.apply_intent(_intent(), expected_plan_hash=review.review_hash)

    assert [item.logical_id for item in review.retiring] == ["realm:relay-old:0"]
    assert review.review_hash != review.plan.plan_hash
    assert "srv-relay" not in review.plan.deployment_contract.server_targets
    assert review.retirement_targets["srv-relay"].host == "198.51.100.30"
    assert result.all_succeeded, result.failure_summary
    assert result.retired == ["realm:relay-old:0"]
    assert result.review_hash == review.review_hash
    assert retired == [("realm:relay-old:0", built["srv-relay"])]
    assert not binding.active
    assert cluster.active_plan_hash == review.plan.plan_hash
    after = runtime.review(SetupDraft.from_intent(_intent()))
    assert after.retiring == ()
    assert after.review_hash == after.plan.plan_hash == cluster.active_plan_hash


def test_apply_streams_one_event_pair_per_action_to_the_runtime_reporter(tmp_path, monkeypatch) -> None:
    cluster, _binding = _cluster_with_old_relay()
    reporter = CaptureReporter()
    runtime = _retiring_runtime(tmp_path, monkeypatch, cluster, [], {}, reporter=reporter)

    review = runtime.review(SetupDraft.from_intent(_intent()))
    runtime.apply_intent(_intent(), expected_plan_hash=review.review_hash)

    started = [event for event in reporter.events if event.type == "plan.action.started"]
    completed = [event for event in reporter.events if event.type == "plan.action.completed"]
    assert len(started) == len(completed) == len(review.plan.resources)
    assert {event.operation_id for event in reporter.events} == {started[0].operation_id}


def test_a_review_that_did_not_see_the_retirement_set_is_refused(tmp_path, monkeypatch) -> None:
    cluster, _binding = _cluster_with_old_relay()
    retired: list[tuple[str, object]] = []
    runtime = _retiring_runtime(tmp_path, monkeypatch, cluster, retired, {})
    plan_hash = runtime.review(SetupDraft.from_intent(_intent())).plan.plan_hash

    with pytest.raises(LocalStateError, match="changed after it was reviewed"):
        runtime.apply_intent(_intent(), expected_plan_hash=plan_hash)
    assert retired == []
    assert cluster.topology_intent is None


def test_review_fails_closed_when_a_retiring_server_profile_is_gone(tmp_path) -> None:
    registry = ServerRegistry(tmp_path / "servers.json")
    _add_servers(registry)
    cluster, _binding = _cluster_with_old_relay("srv-forgotten")

    with pytest.raises(LocalStateError, match="saved profile is gone"):
        SetupRuntime(registry, cluster_loader=lambda: cluster).review(SetupDraft.from_intent(_intent()))


def test_control_attestation_ignores_other_servers_but_tracks_runtime_pins(tmp_path) -> None:
    from meridian.setup.control_plane import control_runtime_attestation

    registry = ServerRegistry(tmp_path / "servers.json")
    _add_servers(registry, relay=True)
    runtime = SetupRuntime(registry, cluster_loader=ClusterConfig)
    base = _intent()
    with_relay = base.model_copy(
        update={
            "transparent_relays": [
                TransparentRelayIntent(
                    id="relay-a",
                    hop_server_refs=["srv-relay"],
                    exit_ref="exit-a",
                    protocol_path_ref="exit-a-reality",
                )
            ]
        }
    )

    def attestation(intent: SetupIntent) -> tuple[str, ResourcePlan]:
        plan = runtime.review(SetupDraft.from_intent(intent)).plan
        control = next(item for item in plan.resources if isinstance(item.payload, ControlPlaneRuntimePayload))
        return control_runtime_attestation(SimpleNamespace(expected_hash=control.desired_hash), plan), plan

    relay_attestation, relay_plan = attestation(with_relay)
    plain_attestation, _ = attestation(base)
    assert "srv-relay" in relay_plan.deployment_contract.server_targets
    assert relay_attestation == plain_attestation

    contract = relay_plan.deployment_contract
    bumped = relay_plan.model_copy(
        update={
            "deployment_contract": contract.model_copy(
                update={"runtime_pins": {**contract.runtime_pins, "remnawave_backend": "9.9.9"}}
            )
        }
    )
    control = next(item for item in bumped.resources if isinstance(item.payload, ControlPlaneRuntimePayload))
    bumped_attestation = control_runtime_attestation(SimpleNamespace(expected_hash=control.desired_hash), bumped)
    assert bumped_attestation != relay_attestation


class _RefusingFence:
    def ensure(self, local) -> None:
        raise FleetFenceError("Another machine changed this fleet.")

    def check(self, local) -> str:
        return "Another machine changed this fleet."

    def acquire(self, local) -> str:
        raise AssertionError("apply must stop before acquiring")

    def renew(self) -> None: ...

    def commit(self, generation: int, plan_hash: str) -> None: ...

    def release(self) -> None: ...


def test_stale_copy_is_refused_before_its_intent_edit_is_saved(tmp_path, monkeypatch) -> None:
    cluster, _binding = _cluster_with_old_relay()
    runtime = _retiring_runtime(tmp_path, monkeypatch, cluster, [], {})
    saved: list[object] = []
    runtime._persist = lambda state: saved.append(state.topology_intent)
    runtime._fence_factory = lambda *_args: _RefusingFence()
    review = runtime.review(SetupDraft.from_intent(_intent()))

    with pytest.raises(FleetFenceError):
        runtime.apply_intent(_intent(), expected_plan_hash=review.review_hash)

    assert saved == []
    assert cluster.topology_intent is None


def test_adding_an_exit_that_sorts_first_never_reruns_the_control_bootstrap(tmp_path) -> None:
    from meridian.setup.control_plane import control_runtime_attestation

    registry = ServerRegistry(tmp_path / "servers.json")
    _add_servers(registry, relay=True)
    runtime = SetupRuntime(registry, cluster_loader=ClusterConfig)
    local_exit = _intent().exits[0].model_copy(update={"server_ref": "srv-control"})
    base = _intent().model_copy(update={"exits": [local_exit]})
    remote_exit = ExitIntent(
        id="aaa-remote",
        server_ref="srv-relay",
        paths=[ProtocolPathIntent(id="aaa-reality", protocol="reality", reality_sni="www.example.com")],
    )
    grown = base.model_copy(update={"exits": [local_exit, remote_exit]})

    def attestation(intent: SetupIntent) -> str:
        plan = runtime.review(SetupDraft.from_intent(intent)).plan
        control = next(item for item in plan.resources if isinstance(item.payload, ControlPlaneRuntimePayload))
        return control_runtime_attestation(SimpleNamespace(expected_hash=control.desired_hash), plan)

    assert attestation(grown) == attestation(base)


def test_surviving_pre_rename_panel_directory_makes_the_control_runtime_drift() -> None:
    """Bootstrap then moves it; the attestation alone would call the runtime converged."""
    from meridian.setup.control_plane import control_runtime_ready

    action = SimpleNamespace(
        resource=SimpleNamespace(logical_id="control", payload=ControlPlaneRuntimePayload(server_ref="srv-control"))
    )
    cluster = ClusterConfig(panel=PanelConfig(api_token="token", server_ip="198.51.100.10", ssh_user="root"))
    entry = ServerEntry(host="198.51.100.10", user="root")
    connection = MagicMock(spec=ServerConnection)
    connection.run.return_value = SimpleNamespace(returncode=1, stdout="", stderr="")

    ready = control_runtime_ready(action, None, cluster, entry, connection, MagicMock())  # type: ignore[arg-type]

    assert ready is False
    assert connection.run.call_args.args[0] == "test ! -e /opt/remnawave"
    connection.get_text.assert_not_called()


def test_self_steal_exit_on_the_control_server_bootstraps_with_a_borrowed_target() -> None:
    """The interim legacy 443 fallback dials the bootstrap SNI; the operator's own name would loop to itself."""
    from meridian.compiler import compile_topology
    from meridian.setup.control_plane import bootstrap_profile_values

    own = ProtocolPathIntent(id="reality-a", protocol="reality", reality_sni="www.example.org", self_steal=True)
    plan = compile_topology(single_exit_intent(exit_ref="srv-control", paths=[own]))

    assert bootstrap_profile_values(plan)[1] == DEFAULT_SNI
