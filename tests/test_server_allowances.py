"""Server allowances and payment dates belong to the reviewed topology."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from meridian.cli import app
from meridian.cluster import ClusterConfig
from meridian.console import set_json_mode, set_quiet_mode
from meridian.core.schema import schema_model
from meridian.core.server_allowances import ServerMetadata, ServerMetadataChange, build_server_metadata_change
from meridian.core.server_calendar import next_payment_date, server_period
from meridian.core.setup import SetupDraft
from meridian.core.topology import SetupIntent
from meridian.reconciler.resources import ResourceExecutionResult
from tests.support.topology import single_exit_intent


@pytest.fixture(autouse=True)
def output_mode() -> Iterator[None]:
    yield
    set_json_mode(False)
    set_quiet_mode(False)


def test_server_metadata_survives_the_review_draft() -> None:
    data = single_exit_intent().model_dump(mode="json")
    server_ref = data["control"]["server_ref"]
    data["server_metadata"] = {
        server_ref: {
            "allowance_bytes": 20 * 1024**4,
            "reset_day": 15,
            "provider": "Example provider",
            "pay_on": "2026-11-15",
            "pay_every": "month",
        }
    }
    intent = SetupIntent.model_validate(data)
    assert intent.model_dump(mode="json")["server_metadata"] == data["server_metadata"]
    assert SetupDraft.from_intent(intent).to_intent() == intent


def test_server_set_applies_the_exact_review_and_returns_metadata(tmp_home: Path) -> None:
    current = single_exit_intent()
    ClusterConfig(topology_intent=current).save()
    review = SimpleNamespace(
        plan=SimpleNamespace(plan_hash="p" * 64, resources=[object()]),
        review_hash="r" * 64,
        retiring=[],
    )
    with (
        patch("meridian.setup.runtime.SetupRuntime.review", return_value=review) as reviewed,
        patch(
            "meridian.setup.runtime.SetupRuntime.apply_intent",
            return_value=ResourceExecutionResult(plan_hash="p" * 64, generation=2),
        ) as apply,
    ):
        result = CliRunner().invoke(
            app,
            [
                "--lang",
                "en",
                "server",
                "set",
                current.control.server_ref,
                "--allowance",
                "20TB",
                "--reset-day",
                "15",
                "--provider",
                "Example provider",
                "--pay-on",
                "2026-11-15",
                "--pay-every",
                "month",
                "--yes",
                "--json",
            ],
        )
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    schema_model("server-set-envelope").model_validate_json(result.stdout)
    assert envelope["command"] == "server.set" and envelope["status"] == "changed"
    assert envelope["data"]["metadata"]["allowance_bytes"] == 20 * 1024**4
    updated = apply.call_args.args[0]
    assert updated.model_dump(exclude={"server_metadata"}) == current.model_dump(exclude={"server_metadata"})
    assert reviewed.call_args.args[0].to_intent() == updated
    assert apply.call_args.kwargs["expected_plan_hash"] == review.review_hash


@pytest.mark.parametrize("size", ["20TB", "20TiB", "20GB", "20GiB", "۱٫۵TiB"])
def test_server_allowances_share_the_client_quota_units(size: str) -> None:
    from meridian.core.client_limits import parse_traffic_limit

    assert build_server_metadata_change(allowance=size).allowance_bytes == parse_traffic_limit(size)


def test_omitted_fields_keep_values_and_clearing_payment_also_clears_repeat() -> None:
    current = ServerMetadata(
        allowance_bytes=100, reset_day=15, provider="Example provider", pay_on=date(2026, 11, 15), pay_every="month"
    )
    changed = build_server_metadata_change(allowance="none", pay_on="none").apply_to(current)
    assert changed.allowance_bytes is None and changed.pay_on is None and changed.pay_every is None
    assert changed.reset_day == 15 and changed.provider == current.provider
    one_off = build_server_metadata_change(pay_every="none").apply_to(current)
    assert one_off.pay_on == current.pay_on and one_off.pay_every is None
    assert build_server_metadata_change(provider="none").apply_to(current).provider == ""
    assert ServerMetadataChange().apply_to(current) == current


@pytest.mark.parametrize(
    "fields",
    [
        {"reset_day": 0},
        {"reset_day": 32},
        {"reset_day": 1.5},
        {"allowance_bytes": 0},
        {"allowance_bytes": 2**63},
        {"pay_on": "2026-02-29"},
        {"pay_on": "2026-11-15T00:00:00Z"},
        {"pay_every": "month"},
        {"pay_on": "2026-11-15", "pay_every": "week"},
        {"provider": "p" * 81},
        {"provider": "Provider\nsecond line"},
        {"provider": "Provider\u202e"},
    ],
)
def test_metadata_validates_sizes_calendar_dates_repeat_and_plain_provider(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ServerMetadata.model_validate(fields)


def test_provider_labels_allow_persian_joiners() -> None:
    assert ServerMetadata(provider="ارائه‌دهنده نمونه").provider == "ارائه‌دهنده نمونه"


@pytest.mark.parametrize(
    "locale,message",
    [
        ("en", "Reset day must be an integer between 1 and 31."),
        ("ru", "День сброса должен быть целым числом от 1 до 31."),
        ("fa", "روز بازنشانی باید عددی صحیح از ۱ تا ۳۱ باشد."),
        ("zh", "重置日必须是 1 到 31 之间的整数。"),
    ],
)
def test_server_input_errors_use_the_selected_human_language(tmp_home: Path, locale: str, message: str) -> None:
    current = single_exit_intent()
    ClusterConfig(topology_intent=current).save()
    result = CliRunner().invoke(
        app, ["--lang", locale, "server", "set", current.control.server_ref, "--reset-day", "0", "--yes"]
    )
    assert result.exit_code == 2, result.output
    assert message in result.output


@pytest.mark.parametrize(
    "moment,day,start,end",
    [
        ("2026-02-27T23:59:59Z", 31, "2026-01-31T00:00:00Z", "2026-02-28T00:00:00Z"),
        ("2026-02-28T00:00:00Z", 31, "2026-02-28T00:00:00Z", "2026-03-31T00:00:00Z"),
        ("2028-02-29T00:00:00Z", 31, "2028-02-29T00:00:00Z", "2028-03-31T00:00:00Z"),
        ("2026-12-31T00:00:00Z", 31, "2026-12-31T00:00:00Z", "2027-01-31T00:00:00Z"),
        ("2026-11-15T01:00:00+03:00", 15, "2026-10-15T00:00:00Z", "2026-11-15T00:00:00Z"),
    ],
)
def test_server_period_uses_utc_and_clamps_each_month_independently(
    moment: str, day: int, start: str, end: str
) -> None:
    assert server_period(day, datetime.fromisoformat(moment)) == (
        datetime.fromisoformat(start),
        datetime.fromisoformat(end),
    )


@pytest.mark.parametrize(
    "anchor,repeat,today,expected",
    [
        ("2026-01-31", "month", "2026-02-28", "2026-02-28"),
        ("2026-01-31", "month", "2026-03-01", "2026-03-31"),
        ("2024-02-29", "year", "2027-02-28", "2027-02-28"),
        ("2024-02-29", "year", "2028-02-29", "2028-02-29"),
        ("2026-11-13", None, "2026-11-14", "2026-11-13"),
    ],
)
def test_payment_repeats_preserve_the_anchor_and_one_off_dates_remain_overdue(
    anchor: str, repeat, today: str, expected: str
) -> None:
    assert next_payment_date(date.fromisoformat(anchor), repeat, date.fromisoformat(today)) == date.fromisoformat(
        expected
    )


def test_metadata_must_name_a_declared_server_and_empty_defaults_keep_existing_serialization() -> None:
    current = single_exit_intent()
    assert "server_metadata" not in current.model_dump(mode="json")
    data = current.model_dump(mode="json")
    data["server_metadata"] = {"srv-unknown": {"allowance_bytes": 100}}
    with pytest.raises(ValidationError, match="undeclared"):
        SetupIntent.model_validate(data)


def test_role_removal_drops_only_metadata_of_servers_that_leave_the_topology() -> None:
    from meridian.core.topology import ExitIntent, ProtocolPathIntent, TransparentRelayIntent
    from meridian.setup.editor import remove_exit_from_intent, remove_relays_from_intent

    current = single_exit_intent()
    data = current.model_dump(mode="json")
    data["exits"].append(
        ExitIntent(
            id="other",
            server_ref="srv-other",
            paths=[ProtocolPathIntent(id="reality-other", protocol="reality", reality_sni="www.example.org")],
        ).model_dump(mode="json")
    )
    data["transparent_relays"] = [
        TransparentRelayIntent(
            id="entry",
            hop_server_refs=["srv-relay"],
            exit_ref=current.exits[0].id,
            protocol_path_ref=current.exits[0].paths[0].id,
        ).model_dump(mode="json")
    ]
    data["server_metadata"] = {
        ref: {"provider": "Example provider"}
        for ref in (
            current.control.server_ref,
            "srv-other",
            "srv-relay",
        )
    }
    intent = SetupIntent.model_validate(data)
    without_exit = remove_exit_from_intent(intent, "other")
    assert set(without_exit.server_metadata) == {current.control.server_ref, "srv-relay"}
    without_relay = remove_relays_from_intent(without_exit, {"entry"})
    assert set(without_relay.server_metadata) == {current.control.server_ref}


def test_metadata_stays_with_a_physical_server_when_an_exit_moves() -> None:
    from meridian.setup.editor import move_exit_in_intent

    current = single_exit_intent(server_metadata={"srv-exit": ServerMetadata(provider="Example provider")})
    moved = move_exit_in_intent(current, current.exits[0].id, "srv-new")
    assert moved.server_metadata == {}
    shared = single_exit_intent(control="srv-exit", server_metadata=current.server_metadata)
    moved = move_exit_in_intent(shared, shared.exits[0].id, "srv-new")
    assert moved.server_metadata == current.server_metadata


def test_configured_metadata_remains_in_fleet_status_when_service_usage_is_unavailable() -> None:
    from meridian.adapters.cluster import topology_from_cluster
    from meridian.core.fleet import FleetSources, build_fleet_status
    from meridian.servers import ServerEntry

    intent = single_exit_intent(
        server_metadata={"srv-exit": ServerMetadata(provider="Example provider", allowance_bytes=100)}
    )
    topology = topology_from_cluster(
        ClusterConfig(topology_intent=intent),
        servers=[
            ServerEntry(host="198.51.100.10", id="srv-control"),
            ServerEntry(host="198.51.100.20", id="srv-exit"),
        ],
    )
    status = build_fleet_status(topology, panel_healthy=False, sources=FleetSources(server_usage="unavailable"))
    server = next(item for item in status.servers if item.id == "srv-exit")
    assert server.metadata == intent.server_metadata["srv-exit"]
    assert status.server_usage == [] and status.summary.needs_attention


def test_a_wizard_role_edit_drops_metadata_only_when_its_server_leaves() -> None:
    current = single_exit_intent(server_metadata={"srv-exit": ServerMetadata(provider="Example provider")})
    data = SetupDraft.from_intent(current).model_dump(mode="json")
    data["roles"]["exits"][0]["server_ref"] = "srv-new"
    data["server_refs"].append("srv-new")
    assert SetupDraft.model_validate(data).to_intent().server_metadata == {}


def test_compiler_changes_only_service_settings_and_maps_agents_by_physical_server() -> None:
    from meridian.compiler import compile_topology
    from meridian.compiler.models import ServiceSettingsPayload
    from tests.support.topology import gateway_fleet_intent

    current = gateway_fleet_intent()
    metadata = {
        ref: ServerMetadata(allowance_bytes=100, provider="Example provider") for ref in current.declared_server_refs()
    }
    data = current.model_dump(mode="json")
    data["server_metadata"] = {ref: value.model_dump(mode="json") for ref, value in metadata.items()}
    before = {item.logical_id: item for item in compile_topology(current).resources}
    after = {item.logical_id: item for item in compile_topology(SetupIntent.model_validate(data)).resources}
    assert before.keys() == after.keys()
    assert [key for key in after if after[key].desired_hash != before[key].desired_hash] == ["settings:service"]
    settings = after["settings:service"].payload
    assert isinstance(settings, ServiceSettingsPayload)
    assert {ref: value.node_id for ref, value in settings.servers.items()} == {
        item.server_ref: item.id for item in [*current.exits, *current.routing_gateways]
    }


def test_server_list_and_fleet_export_keep_metadata_keyed_to_saved_names(tmp_home: Path) -> None:
    from meridian import config
    from meridian.servers import ServerEntry, ServerRegistry
    from meridian.setup.fleet_document import dump_fleet_document, intent_from_document, parse_fleet_document

    registry = ServerRegistry(config.SERVER_PROFILES_FILE)
    registry.add(ServerEntry(host="198.51.100.10", id="srv-control", name="control"))
    registry.add(ServerEntry(host="198.51.100.20", id="srv-exit", name="edge"))
    metadata = ServerMetadata(
        allowance_bytes=100, provider="Example provider", pay_on=date(2026, 11, 15), pay_every="month"
    )
    intent = single_exit_intent(server_metadata={"srv-exit": metadata})
    ClusterConfig(topology_intent=intent).save()
    listed = CliRunner().invoke(app, ["server", "list", "--json"])
    assert listed.exit_code == 0, listed.output
    schema_model("server-list-envelope").model_validate_json(listed.stdout)
    edge = next(item for item in json.loads(listed.stdout)["data"]["servers"] if item["name"] == "edge")
    assert edge["declared"] and edge["metadata"] == metadata.model_dump(mode="json")
    exported = CliRunner().invoke(app, ["fleet", "export", "--json"])
    assert exported.exit_code == 0, exported.output
    document = json.loads(exported.stdout)["data"]["document"]
    assert document["server_metadata"] == {"edge": metadata.model_dump(mode="json")}
    imported = intent_from_document(parse_fleet_document(dump_fleet_document(document)), intent, registry)
    assert imported == intent


@pytest.mark.parametrize(
    "options",
    [
        ["--reset-day", "0"],
        ["--reset-day", "32"],
        ["--reset-day", "bad"],
        ["--allowance", "8388608TiB"],
        ["--pay-on", "2026-02-29"],
        ["--pay-every", "month"],
        ["--provider", "first\nsecond"],
        ["--pay-on", "none", "--pay-every", "year"],
    ],
)
def test_invalid_set_values_return_plain_user_envelopes_before_review(tmp_home: Path, options: list[str]) -> None:
    current = single_exit_intent()
    ClusterConfig(topology_intent=current).save()
    with patch("meridian.setup.runtime.SetupRuntime.review") as review:
        result = CliRunner().invoke(app, ["server", "set", current.control.server_ref, *options, "--yes", "--json"])
    assert result.exit_code == 2, result.output
    schema_model("server-set-envelope").model_validate_json(result.stdout)
    assert json.loads(result.stdout)["errors"][0]["message"]
    review.assert_not_called()


def test_a_json_metadata_change_requires_yes_before_review(tmp_home: Path) -> None:
    current = single_exit_intent()
    ClusterConfig(topology_intent=current).save()
    with patch("meridian.setup.runtime.SetupRuntime.review") as review:
        result = CliRunner().invoke(app, ["server", "set", current.control.server_ref, "--allowance", "20TB", "--json"])
    assert result.exit_code == 2, result.output
    assert "--yes" in json.loads(result.stdout)["errors"][0]["message"]
    review.assert_not_called()


@pytest.mark.parametrize("dry_run", [True, False], ids=["dry-run", "declined"])
def test_reviewed_metadata_does_not_write_when_previewed_or_declined(tmp_home: Path, dry_run: bool) -> None:
    current = single_exit_intent()
    ClusterConfig(topology_intent=current).save()
    review = SimpleNamespace(
        plan=SimpleNamespace(plan_hash="p" * 64, resources=[object()]), review_hash="r" * 64, retiring=[]
    )
    with (
        patch("meridian.setup.runtime.SetupRuntime.review", return_value=review),
        patch("meridian.setup.runtime.SetupRuntime.apply_intent") as apply,
        patch("meridian.console.has_tty", return_value=True),
        patch("meridian.console.confirm", return_value=False),
    ):
        options = ["--dry-run", "--json"] if dry_run else []
        result = CliRunner().invoke(
            app, ["--lang", "en", "server", "set", current.control.server_ref, "--allowance", "20TB", *options]
        )
    assert result.exit_code == (0 if dry_run else 1), result.output
    assert ClusterConfig.load().topology_intent == current
    apply.assert_not_called()
    if dry_run:
        schema_model("server-set-envelope").model_validate_json(result.stdout)
        assert json.loads(result.stdout)["data"]["review"]["review_hash"] == review.review_hash


def test_server_set_finds_a_server_by_the_role_name_fleet_status_shows() -> None:
    from meridian.commands.server_set import _server_ref
    from meridian.core.topology import TransparentRelayIntent

    current = single_exit_intent()
    data = current.model_dump(mode="json")
    data["transparent_relays"] = [
        TransparentRelayIntent(
            id="entry",
            hop_server_refs=["srv-relay"],
            exit_ref=current.exits[0].id,
            protocol_path_ref=current.exits[0].paths[0].id,
        ).model_dump(mode="json")
    ]
    intent = SetupIntent.model_validate(data)
    registry = SimpleNamespace(find=lambda _query: None)

    assert _server_ref(intent, registry, current.exits[0].id) == current.exits[0].server_ref
    assert _server_ref(intent, registry, "entry") == "srv-relay"
