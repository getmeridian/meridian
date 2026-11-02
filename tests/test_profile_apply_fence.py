"""Every apply checks declared profile grants before the fleet fence or resource writes."""

from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from meridian import i18n
from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.commands._helpers import apply_reviewed_intent
from meridian.compiler import compile_topology
from meridian.compiler.deployment import deployment_server_refs
from meridian.compiler.models import NodeWorkloadPayload, ServiceSettingsPayload
from meridian.console import set_json_mode, set_quiet_mode
from meridian.control.client import ControlClient, ControlServiceError
from meridian.control.models import ServiceSettings, UserCreate
from meridian.core.access_profiles import profile_declarations_hash
from meridian.core.errors import LocalStateError, MeridianError
from meridian.core.reporters import NoopReporter
from meridian.core.setup import SetupDraft
from meridian.core.topology import AccessProfileIntent, ProtocolPathIntent, SetupIntent, TransparentRelayIntent
from meridian.reconciler.agent_spec import build_node_spec
from meridian.reconciler.meridian_drivers import ServiceSettingsDriver
from meridian.reconciler.resources import ResourceExecutionResult, build_resource_actions
from meridian.reconciler.workloads import LocalRealityKeyFactory
from meridian.servers import ServerEntry, ServerRegistry
from meridian.setup.runtime import SetupRuntime, SetupVerification
from tests.support.control_service import ADMIN_TOKEN, serve_control
from tests.support.mock_connection import MockConnection
from tests.support.text import unstyle
from tests.support.topology import GATEWAY_SERVERS, gateway_fleet_intent, reality_path, single_exit_intent


@pytest.fixture(autouse=True)
def command_modes(monkeypatch):
    monkeypatch.setattr(i18n, "_current", "en")
    monkeypatch.setattr(i18n, "_requested", None)
    yield
    set_json_mode(False)
    set_quiet_mode(False)


def profile_intent(grants: list[str] | None = None) -> SetupIntent:
    return single_exit_intent(
        users=["person"],
        access_profiles=[AccessProfileIntent(id="limited", grants=["exit:exit-a"] if grants is None else grants)],
    )


def registry_at(path: Path) -> ServerRegistry:
    registry = ServerRegistry(path)
    registry.add(ServerEntry(id="srv-control", host="198.51.100.10", auth_state="validated"))
    registry.add(ServerEntry(id="srv-exit", host="198.51.100.20", auth_state="validated"))
    return registry


def seed_profiles(admin: ControlClient, intent: SetupIntent):
    keys = LocalRealityKeyFactory()("srv-exit")
    plan = compile_topology(intent)
    for resource in plan.resources:
        payload = resource.payload
        if isinstance(payload, NodeWorkloadPayload):
            admin.put_node(
                payload.workload_ref,
                build_node_spec(payload.profile, keys, arch="x86_64", access_profiles=payload.access_profiles),
            )
        elif isinstance(payload, ServiceSettingsPayload):
            admin.put_settings(ServiceSettings(profile_title="Meridian", access_profiles=payload.access_profiles))
    admin.create_user(UserCreate(username="person", profiles=["limited"]))
    return keys


@pytest.mark.parametrize("entrypoint", ["draft", "intent", "imperative"])
@pytest.mark.parametrize("edit", ["remove-last", "empty-grants"])
@pytest.mark.parametrize("fingerprinted", [False, True], ids=["legacy-map", "reviewed-declarations"])
def test_unreviewed_apply_refuses_saved_profile_edits_before_fence_or_node_writes(
    tmp_path: Path, tmp_home: Path, entrypoint: str, edit: str, fingerprinted: bool
):
    from meridian import config

    registry = registry_at(config.SERVER_PROFILES_FILE)
    wanted = single_exit_intent(users=["person"]) if edit == "remove-last" else profile_intent([])
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        keys = seed_profiles(admin, profile_intent())
        if fingerprinted:
            admin.put_settings(
                ServiceSettings.model_validate(
                    {
                        **admin.settings().to_data(),
                        "access_profile_declarations_hash": profile_declarations_hash(profile_intent().access_profiles),
                    }
                ),
                expected_access_hash=admin.access_profiles().access_hash,
            )
        node = admin.node("exit-a")
        cluster = ClusterConfig(panel=PanelConfig(url=url, api_token=ADMIN_TOKEN), topology_intent=wanted)
        cluster.save()
        saved = config.CLUSTER_CONFIG.read_bytes()
        fence, persist = MagicMock(), MagicMock(side_effect=lambda state: state.save())
        runtime = SetupRuntime(
            registry,
            cluster_loader=lambda: cluster,
            persist=persist,
            connection_builder=lambda _entry: MockConnection(),
            fence_factory=lambda *_args: fence,
        )

        def write_nodes(plan, *_args, **_kwargs):
            for resource in plan.resources:
                payload = resource.payload
                if isinstance(payload, NodeWorkloadPayload):
                    admin.put_node(
                        payload.workload_ref,
                        build_node_spec(payload.profile, keys, arch="x86_64", access_profiles=payload.access_profiles),
                    )
            return ResourceExecutionResult(plan_hash=plan.plan_hash, generation=1)

        with (
            patch("meridian.setup.runtime.refuse_legacy_deployments"),
            patch.object(runtime, "_build_drivers", return_value={}),
            patch("meridian.setup.runtime.execute_resource_plan", side_effect=write_nodes) as execute,
        ):
            if entrypoint == "imperative":
                with (
                    patch("meridian.commands._helpers.reviewed_runtime", return_value=runtime),
                    patch("meridian.commands._helpers.apply_progress", return_value=nullcontext(NoopReporter())),
                    pytest.raises(typer.Exit) as refused,
                ):
                    apply_reviewed_intent(cluster, registry, wanted, failure="Apply failed.", hint="Retry.")
                assert refused.value.exit_code == 2
            else:
                with pytest.raises(LocalStateError) as refused:
                    if entrypoint == "intent":
                        runtime.apply_intent(wanted)
                    else:
                        draft = SetupDraft.from_intent(wanted)
                        runtime.apply(draft.model_copy(update={"review_hash": runtime.review(draft).review_hash}))
                assert refused.value.category == "user"
                assert "meridian profile" in refused.value.hint and "meridian fleet import" in refused.value.hint
                assert "meridian apply" in refused.value.hint
        fence.ensure.assert_not_called()
        execute.assert_not_called()
        persist.assert_not_called()
        assert config.CLUSTER_CONFIG.read_bytes() == saved
        assert admin.node("exit-a") == node
        assert admin.get_user("person").profiles == ["limited"]


@pytest.mark.parametrize("used", [False, True])
def test_unreviewed_apply_refuses_unavailable_service_for_profile_history_or_new_declarations(
    tmp_path: Path, tmp_home: Path, used: bool
):
    wanted = single_exit_intent() if used else profile_intent()
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.10/control", api_token=ADMIN_TOKEN),
        topology_intent=single_exit_intent(),
        access_profiles_used=used,
    )
    fence, persist = MagicMock(), MagicMock()
    runtime = SetupRuntime(
        registry_at(tmp_path / "servers.json"),
        cluster_loader=lambda: cluster,
        persist=persist,
        connection_builder=lambda _entry: MockConnection(),
        fence_factory=lambda *_args: fence,
    )
    with (
        patch("meridian.setup.runtime.refuse_legacy_deployments"),
        patch("meridian.control.client.ControlClient._call", side_effect=ControlServiceError("Service unavailable.")),
        patch.object(runtime, "_build_drivers", return_value={}),
        patch(
            "meridian.setup.runtime.execute_resource_plan",
            return_value=ResourceExecutionResult(plan_hash="a" * 64, generation=1),
        ) as execute,
        pytest.raises(MeridianError, match="profile") as refused,
    ):
        runtime.apply_intent(wanted)
    assert refused.value.category == "system"
    fence.ensure.assert_not_called()
    execute.assert_not_called()
    persist.assert_not_called()


def test_unreviewed_apply_without_any_profile_history_can_repair_an_unavailable_service(tmp_path: Path, tmp_home: Path):
    cluster = ClusterConfig(
        panel=PanelConfig(url="https://198.51.100.10/control", api_token=ADMIN_TOKEN),
        topology_intent=single_exit_intent(),
    )
    fence = MagicMock()
    runtime = SetupRuntime(
        registry_at(tmp_path / "servers.json"),
        cluster_loader=lambda: cluster,
        persist=lambda _state: None,
        connection_builder=lambda _entry: MockConnection(),
        fence_factory=lambda *_args: fence,
    )
    with (
        patch("meridian.setup.runtime.refuse_legacy_deployments"),
        patch("meridian.control.client.ControlClient._call", side_effect=ControlServiceError("Service unavailable.")),
        patch.object(runtime, "_build_drivers", return_value={}),
        patch(
            "meridian.setup.runtime.execute_resource_plan",
            return_value=ResourceExecutionResult(plan_hash="a" * 64, generation=1),
        ),
    ):
        assert runtime.apply_intent(single_exit_intent()).all_succeeded
    fence.ensure.assert_called_once()
    assert cluster.has_used_access_profiles is False


def expansion_intents(edit: str) -> tuple[SetupIntent, SetupIntent]:
    before = profile_intent()
    if edit == "no-hysteria2":
        before = single_exit_intent(
            users=["person"],
            access_profiles=before.access_profiles,
            paths=[reality_path(), ProtocolPathIntent(id="udp", protocol="hysteria2", tls_sni="198.51.100.20")],
        )
        return before, profile_intent()
    if edit == "gateway-blueprint":
        before = SetupIntent.model_validate(
            {**gateway_fleet_intent().to_data(), "access_profiles": [{"id": "limited", "grants": ["gateway:gw"]}]}
        )
        data = before.to_data()
        data["routing_gateways"][0]["bridge_path_ref"] = "de-reality"
        return before, SetupIntent.model_validate(data)
    relay = TransparentRelayIntent(
        id="relay",
        exit_ref="exit-a",
        protocol_path_ref="reality-a",
        hop_server_refs=["srv-relay"],
        reality_sni="relay.example.org",
    )
    with_relay = SetupIntent.model_validate({**before.to_data(), "transparent_relays": [relay.to_data()]})
    if edit == "relay-add":
        return before, with_relay
    if edit == "relay-remove":
        return with_relay, before
    data = with_relay.to_data()
    data["transparent_relays"][0]["reality_sni"] = "other.example.org"
    return with_relay, SetupIntent.model_validate(data)


@pytest.mark.parametrize("edit", ["no-hysteria2", "relay-add", "relay-sni", "relay-remove", "gateway-blueprint"])
def test_unreviewed_apply_allows_topology_to_expand_unchanged_exit_and_gateway_grants(
    tmp_path: Path, tmp_home: Path, edit: str
):
    before, after = expansion_intents(edit)
    old_plan, new_plan = compile_topology(before), compile_topology(after)
    old_settings = next(row.payload for row in old_plan.resources if isinstance(row.payload, ServiceSettingsPayload))
    new_settings = next(row.payload for row in new_plan.resources if isinstance(row.payload, ServiceSettingsPayload))
    assert old_settings.access_profiles != new_settings.access_profiles
    assert old_settings.access_profile_declarations_hash == new_settings.access_profile_declarations_hash
    registry = ServerRegistry(tmp_path / "servers.json")
    addresses = {
        "srv-control": "198.51.100.10",
        "srv-exit": "198.51.100.20",
        "srv-relay": "203.0.113.10",
        **GATEWAY_SERVERS,
    }
    for ref in sorted(deployment_server_refs(before) | deployment_server_refs(after)):
        registry.add(ServerEntry(id=ref, host=addresses[ref]))
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        admin.put_settings(
            ServiceSettings(
                profile_title="Meridian",
                access_profiles=old_settings.access_profiles,
                access_profile_declarations_hash=old_settings.access_profile_declarations_hash,
            ),
            expected_access_hash=admin.access_profiles().access_hash,
        )
        person = admin.create_user(UserCreate(username="person", profiles=["limited"]))
        cluster = ClusterConfig(panel=PanelConfig(url=url, api_token=ADMIN_TOKEN), topology_intent=before)
        fence = MagicMock()
        runtime = SetupRuntime(
            registry,
            cluster_loader=lambda: cluster,
            persist=lambda _state: None,
            connection_builder=lambda _entry: MockConnection(),
            fence_factory=lambda *_args: fence,
        )

        def save_settings(plan, *_args, **_kwargs):
            action = next(
                row for row in build_resource_actions(plan, 2) if row.resource.logical_id == "settings:service"
            )
            context = SimpleNamespace(
                cluster=cluster,
                access_review_hash=None,
                client=lambda: ControlClient(url, ADMIN_TOKEN, report_mutations=False),
            )
            ServiceSettingsDriver(context).apply(action, None)
            return ResourceExecutionResult(plan_hash=plan.plan_hash, generation=2)

        with (
            patch("meridian.setup.runtime.refuse_legacy_deployments"),
            patch.object(runtime, "_build_drivers", return_value={}),
            patch("meridian.setup.runtime.execute_resource_plan", side_effect=save_settings),
        ):
            assert runtime.apply_intent(after).all_succeeded
        fence.ensure.assert_called_once()
        assert admin.settings().access_profiles == new_settings.access_profiles
        assert admin.settings().access_profile_declarations_hash == old_settings.access_profile_declarations_hash
        assert admin.get_user("person").profiles == person.profiles
        assert admin.get_user("person").uuid == person.uuid


@pytest.mark.parametrize("credentials", [False, True], ids=["new-fleet", "empty-service"])
@pytest.mark.parametrize("named", [False, True], ids=["no-profiles", "named-profiles"])
def test_fresh_apply_bootstraps_and_records_only_named_profile_declarations(
    tmp_path: Path, tmp_home: Path, credentials: bool, named: bool
):
    wanted = profile_intent() if named else single_exit_intent()
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        cluster = ClusterConfig(panel=PanelConfig(url=url, api_token=ADMIN_TOKEN) if credentials else PanelConfig())
        runtime = SetupRuntime(
            registry_at(tmp_path / "servers.json"),
            cluster_loader=lambda: cluster,
            persist=lambda _state: None,
            connection_builder=lambda _entry: MockConnection(),
            fence_factory=lambda *_args: MagicMock(),
        )

        def save_settings(plan, *_args, **_kwargs):
            action = next(
                row for row in build_resource_actions(plan, 1) if row.resource.logical_id == "settings:service"
            )
            context = SimpleNamespace(
                cluster=cluster,
                access_review_hash=None,
                client=lambda: ControlClient(url, ADMIN_TOKEN, report_mutations=False),
            )
            ServiceSettingsDriver(context).apply(action, None)
            return ResourceExecutionResult(plan_hash=plan.plan_hash, generation=1)

        with (
            patch("meridian.setup.runtime.refuse_legacy_deployments"),
            patch.object(runtime, "_build_drivers", return_value={}),
            patch("meridian.setup.runtime.execute_resource_plan", side_effect=save_settings),
        ):
            assert runtime.apply_intent(wanted).all_succeeded
        assert cluster.has_used_access_profiles is named
        assert admin.settings().access_profile_declarations_hash == (
            profile_declarations_hash(wanted.access_profiles) if named else None
        )


@pytest.mark.parametrize("locale", ["en", "ru", "fa", "zh"])
@pytest.mark.parametrize("entrypoint", ["setup-intent", "setup-restart", "fleet-contact"])
def test_setup_and_imperative_cli_share_the_profile_refusal_and_native_guidance(
    tmp_path: Path, tmp_home: Path, locale: str, entrypoint: str
):
    from meridian import config

    registry_at(config.SERVER_PROFILES_FILE)
    wanted = profile_intent([])
    source = tmp_path / "intent.json"
    source.write_text(wanted.model_dump_json())
    argv = (
        ["fleet", "contact", "--text", "Help", "--yes", "--json"]
        if entrypoint == "fleet-contact"
        else ["setup", "--intent", str(source), "--yes", *(["--restart"] if entrypoint == "setup-restart" else [])]
    )
    with serve_control(tmp_path) as url, ControlClient(url, ADMIN_TOKEN, report_mutations=False) as admin:
        seed_profiles(admin, profile_intent())
        ClusterConfig(panel=PanelConfig(url=url, api_token=ADMIN_TOKEN), topology_intent=wanted).save()
        saved = config.CLUSTER_CONFIG.read_bytes()
        fence = MagicMock()
        with (
            patch("meridian.setup.runtime.refuse_legacy_deployments"),
            patch("meridian.setup.runtime.connection_for_entry", return_value=MockConnection()),
            patch("meridian.setup.runtime.SetupRuntime._default_fence", return_value=fence),
            patch("meridian.setup.runtime.SetupRuntime._build_drivers", return_value={}),
            patch("meridian.setup.runtime.execute_resource_plan") as execute,
            patch(
                "meridian.setup.runtime.SetupRuntime.verify",
                return_value=SetupVerification(plan_hash="a" * 64, node_count=1, subscription_urls={}),
            ),
        ):
            execute.side_effect = lambda plan, *_args, **_kw: ResourceExecutionResult(
                plan_hash=plan.plan_hash, generation=1
            )
            reply = CliRunner().invoke(app, ["--lang", locale, *argv])
        assert reply.exit_code == 2, reply.output
        text = " ".join(unstyle(reply.output).split())
        if entrypoint == "fleet-contact":
            error = json.loads(reply.stdout)["errors"][0]
            assert error["message"] == i18n.t("profiles.review_required")
            assert error["hint"] == i18n.t("profiles.review_required_hint")
        else:
            assert i18n.t("profiles.review_required") in text
        for command in ("meridian profile", "meridian fleet import", "meridian apply"):
            assert command in text
        fence.ensure.assert_not_called()
        execute.assert_not_called()
        assert config.CLUSTER_CONFIG.read_bytes() == saved
