"""Fleet routing reads local intent and applies only the choice that was reviewed."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from meridian.cli import app
from meridian.cluster import ClusterConfig
from meridian.console import set_json_mode, set_quiet_mode
from meridian.core.errors import MeridianError
from meridian.core.schema import schema_model
from meridian.core.topology import DeliveryIntent
from meridian.reconciler.resources import ResourceExecutionResult
from tests.support.topology import single_exit_intent


@pytest.fixture(autouse=True)
def _output_mode() -> Iterator[None]:
    yield
    set_json_mode(False)
    set_quiet_mode(False)


def test_fleet_routing_prints_the_current_policy_and_both_explanations(tmp_home: Path) -> None:
    ClusterConfig(topology_intent=single_exit_intent()).save()
    with patch("meridian.setup.runtime.SetupRuntime.review") as review:
        result = CliRunner().invoke(app, ["--lang", "en", "fleet", "routing"])
    assert result.exit_code == 0, result.output
    assert "ru-split" in result.output and "full" in result.output
    assert "Russian" in result.output and "private" in result.output and "BitTorrent" in result.output
    review.assert_not_called()


@pytest.mark.parametrize("argv", [["fleet", "routing", "--json"], ["--json", "fleet", "routing"]])
def test_fleet_routing_json_reads_the_policy_without_a_review(tmp_home: Path, argv: list[str]) -> None:
    ClusterConfig(topology_intent=single_exit_intent()).save()
    result = CliRunner().invoke(app, argv)
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    assert envelope["command"] == "fleet.routing"
    assert envelope["data"]["client_routing"] == "ru-split"
    assert {choice["policy"] for choice in envelope["data"]["choices"]} == {"ru-split", "full"}
    assert all(choice["description"] for choice in envelope["data"]["choices"])


@pytest.mark.parametrize("policy", ["ir-split", "cn-split", "other"])
def test_fleet_routing_refuses_unreviewed_presets_before_review(tmp_home: Path, policy: str) -> None:
    ClusterConfig(topology_intent=single_exit_intent()).save()
    with patch("meridian.setup.runtime.SetupRuntime.review") as review:
        result = CliRunner().invoke(app, ["--lang", "en", "fleet", "routing", policy, "--yes", "--json"])
    assert result.exit_code == 2
    envelope = json.loads(result.stdout)
    assert "ru-split" in envelope["errors"][0]["message"] and "full" in envelope["errors"][0]["message"]
    review.assert_not_called()


def test_fleet_routing_json_requires_yes_before_review(tmp_home: Path) -> None:
    ClusterConfig(topology_intent=single_exit_intent()).save()
    with patch("meridian.setup.runtime.SetupRuntime.review") as review:
        result = CliRunner().invoke(app, ["fleet", "routing", "full", "--json"])
    assert result.exit_code == 2
    assert "--yes" in json.loads(result.stdout)["errors"][0]["message"]
    review.assert_not_called()


def routing_review() -> SimpleNamespace:
    return SimpleNamespace(
        plan=SimpleNamespace(plan_hash="p" * 64, resources=[object()]),
        review_hash="r" * 64,
        retiring=[SimpleNamespace(logical_id="endpoint:gone")],
    )


@pytest.mark.parametrize("previous,selected", [("ru-split", "full"), ("full", "ru-split")])
def test_routing_applies_only_the_reviewed_delivery_edit_and_returns_a_valid_envelope(
    tmp_home: Path, previous: str, selected: str
) -> None:
    current = single_exit_intent(
        delivery=DeliveryIntent(client_routing=previous, update_interval_hours=24, profile_title="Ours")
    )
    ClusterConfig(topology_intent=current).save()
    review = routing_review()
    with (
        patch("meridian.setup.runtime.SetupRuntime.review", return_value=review) as reviewed,
        patch(
            "meridian.setup.runtime.SetupRuntime.apply_intent",
            return_value=ResourceExecutionResult(plan_hash="p" * 64, generation=2),
        ) as apply,
    ):
        result = CliRunner().invoke(app, ["fleet", "routing", selected, "--yes", "--json"])
    assert result.exit_code == 0, result.output
    schema_model("fleet-routing-envelope").model_validate_json(result.stdout)
    envelope = json.loads(result.stdout)
    assert envelope["status"] == "changed"
    assert envelope["data"]["client_routing"] == selected
    assert envelope["data"]["applied"]["generation"] == 2
    assert envelope["data"]["review"]["retiring_resources"] == ["endpoint:gone"]
    apply.assert_called_once()
    updated = apply.call_args.args[0]
    assert updated.delivery.client_routing == selected
    assert updated.delivery.update_interval_hours == 24 and updated.delivery.profile_title == "Ours"
    assert updated.model_dump(exclude={"delivery"}) == current.model_dump(exclude={"delivery"})
    assert reviewed.call_args.args[0].to_intent() == updated
    assert apply.call_args.kwargs["expected_plan_hash"] == review.review_hash


def test_routing_dry_run_returns_the_review_and_preserves_the_saved_intent(tmp_home: Path) -> None:
    current = single_exit_intent()
    ClusterConfig(topology_intent=current).save()
    with (
        patch("meridian.setup.runtime.SetupRuntime.review", return_value=routing_review()) as review,
        patch("meridian.setup.runtime.SetupRuntime.apply_intent") as apply,
    ):
        result = CliRunner().invoke(app, ["fleet", "routing", "full", "--dry-run", "--json"])
    assert result.exit_code == 0, result.output
    schema_model("fleet-routing-envelope").model_validate_json(result.stdout)
    envelope = json.loads(result.stdout)
    assert envelope["status"] == "ok" and envelope["data"]["dry_run"]
    assert envelope["data"]["client_routing"] == "full"
    assert envelope["data"]["review"]["review_hash"] == "r" * 64
    review.assert_called_once()
    apply.assert_not_called()
    assert ClusterConfig.load().topology_intent == current


def test_declining_routing_review_applies_nothing_and_preserves_the_saved_intent(tmp_home: Path) -> None:
    current = single_exit_intent()
    ClusterConfig(topology_intent=current).save()
    with (
        patch("meridian.setup.runtime.SetupRuntime.review", return_value=routing_review()),
        patch("meridian.setup.runtime.SetupRuntime.apply_intent") as apply,
        patch("meridian.console.has_tty", return_value=True),
        patch("meridian.console.confirm", return_value=False),
    ):
        result = CliRunner().invoke(app, ["--lang", "en", "fleet", "routing", "full"])
    assert result.exit_code == 1, result.output
    assert "ru-split" in result.output and "full" in result.output and "endpoint:gone" in result.output
    assert "r" * 64 in result.output
    apply.assert_not_called()
    assert ClusterConfig.load().topology_intent == current


@pytest.mark.parametrize("policy", ["ru-split", "full"])
def test_selecting_the_current_routing_policy_needs_no_confirmation_or_apply(tmp_home: Path, policy: str) -> None:
    ClusterConfig(topology_intent=single_exit_intent(delivery=DeliveryIntent(client_routing=policy))).save()
    with (
        patch("meridian.setup.runtime.SetupRuntime.review") as review,
        patch("meridian.setup.runtime.SetupRuntime.apply_intent") as apply,
    ):
        result = CliRunner().invoke(app, ["fleet", "routing", policy, "--json"])
    assert result.exit_code == 0, result.output
    schema_model("fleet-routing-envelope").model_validate_json(result.stdout)
    envelope = json.loads(result.stdout)
    assert envelope["status"] == "no_changes" and envelope["data"]["client_routing"] == policy
    review.assert_not_called()
    apply.assert_not_called()


@pytest.mark.parametrize("stage", ["review", "apply_intent"])
def test_routing_failure_uses_the_advertised_system_error_envelope(tmp_home: Path, stage: str) -> None:
    ClusterConfig(topology_intent=single_exit_intent()).save()
    with (
        patch("meridian.setup.runtime.SetupRuntime.review", return_value=routing_review()),
        patch("meridian.setup.runtime.SetupRuntime.apply_intent"),
        patch(
            f"meridian.setup.runtime.SetupRuntime.{stage}",
            side_effect=MeridianError("Evidence unavailable", category="system"),
        ),
    ):
        result = CliRunner().invoke(app, ["fleet", "routing", "full", "--yes", "--json"])
    assert result.exit_code == 3, result.output
    schema_model("fleet-routing-envelope").model_validate_json(result.stdout)
    assert json.loads(result.stdout)["errors"][0]["message"] == "Evidence unavailable"


def test_routing_dry_run_requires_a_policy(tmp_home: Path) -> None:
    result = CliRunner().invoke(app, ["fleet", "routing", "--dry-run", "--json"])
    assert result.exit_code == 2, result.output
    schema_model("fleet-routing-envelope").model_validate_json(result.stdout)
    assert "--dry-run" in json.loads(result.stdout)["errors"][0]["message"]
