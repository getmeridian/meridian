"""Fleet contact CLI reads local intent and applies only a reviewed presentation edit."""

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
from meridian.core.schema import schema_model
from meridian.core.topology import DeliveryIntent
from meridian.reconciler.resources import ResourceExecutionResult
from tests.support.text import unstyle
from tests.support.topology import single_exit_intent


@pytest.fixture(autouse=True)
def _output_mode() -> Iterator[None]:
    yield
    set_json_mode(False)
    set_quiet_mode(False)


def review_result() -> SimpleNamespace:
    return SimpleNamespace(
        plan=SimpleNamespace(plan_hash="a" * 64, resources=[object()]), review_hash="b" * 64, retiring=[]
    )


def test_fleet_contact_reads_without_review_and_has_discoverable_help(tmp_home: Path) -> None:
    ClusterConfig(topology_intent=single_exit_intent()).save()
    with patch("meridian.setup.runtime.SetupRuntime.review") as review:
        result = CliRunner().invoke(app, ["fleet", "contact", "--json"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    schema_model("fleet-contact-envelope").model_validate(envelope)
    assert envelope["data"]["owner_contact"] == {"text": "", "link": ""}
    review.assert_not_called()
    help_result = CliRunner().invoke(app, ["fleet", "contact", "--help"])
    assert "--text" in unstyle(help_result.output) and "--link" in unstyle(help_result.output)


@pytest.mark.parametrize("dry_run", [True, False])
def test_contact_edit_preserves_every_other_intent_field_and_uses_the_review_hash(
    tmp_home: Path, dry_run: bool
) -> None:
    current = single_exit_intent(
        delivery=DeliveryIntent(profile_title="Ours", update_interval_hours=24, client_routing="full")
    )
    ClusterConfig(topology_intent=current).save()
    with (
        patch("meridian.setup.runtime.SetupRuntime.review", return_value=review_result()) as review,
        patch(
            "meridian.setup.runtime.SetupRuntime.apply_intent",
            return_value=ResourceExecutionResult(plan_hash="a" * 64, generation=2),
        ) as apply,
    ):
        result = CliRunner().invoke(
            app,
            [
                "fleet",
                "contact",
                "--text",
                "Ask for help",
                "--link",
                "HTTPS://EXAMPLE.COM:443/help",
                "--json",
                "--dry-run" if dry_run else "--yes",
            ],
        )
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    schema_model("fleet-contact-envelope").model_validate(envelope)
    assert envelope["data"]["owner_contact"] == {"text": "Ask for help", "link": "https://example.com/help"}
    updated = review.call_args.args[0].to_intent()
    assert updated.model_dump(exclude={"delivery"}) == current.model_dump(exclude={"delivery"})
    assert updated.delivery.model_dump(exclude={"owner_contact"}) == current.delivery.model_dump()
    if dry_run:
        apply.assert_not_called()
        assert ClusterConfig.load().topology_intent == current
    else:
        assert apply.call_args.args[0] == updated
        assert apply.call_args.kwargs["expected_plan_hash"] == "b" * 64


def test_contact_json_change_requires_confirmation_before_review(tmp_home: Path) -> None:
    ClusterConfig(topology_intent=single_exit_intent()).save()
    with patch("meridian.setup.runtime.SetupRuntime.review") as review:
        result = CliRunner().invoke(app, ["fleet", "contact", "--text", "Ask for help", "--json"])
    assert result.exit_code == 2
    assert "--yes" in json.loads(result.stdout)["errors"][0]["message"]
    review.assert_not_called()


def test_declining_contact_review_preserves_the_fleet(tmp_home: Path) -> None:
    current = single_exit_intent()
    ClusterConfig(topology_intent=current).save()
    with (
        patch("meridian.setup.runtime.SetupRuntime.review", return_value=review_result()),
        patch("meridian.setup.runtime.SetupRuntime.apply_intent") as apply,
        patch("meridian.console.has_tty", return_value=True),
        patch("meridian.console.confirm", return_value=False),
    ):
        result = CliRunner().invoke(
            app, ["--lang", "en", "fleet", "contact", "--link", "tg://resolve?domain=sample_support"]
        )
    assert result.exit_code == 1
    assert "sample_support" in result.output and "b" * 64 in result.output
    apply.assert_not_called()
    assert ClusterConfig.load().topology_intent == current


def test_contact_clears_only_the_explicit_field(tmp_home: Path) -> None:
    delivery = DeliveryIntent.model_validate(
        {"owner_contact": {"text": "Ask for help", "link": "mailto:help@example.com"}}
    )
    ClusterConfig(topology_intent=single_exit_intent(delivery=delivery)).save()
    with (
        patch("meridian.setup.runtime.SetupRuntime.review", return_value=review_result()) as review,
        patch("meridian.setup.runtime.SetupRuntime.apply_intent") as apply,
    ):
        result = CliRunner().invoke(app, ["fleet", "contact", "--link", "none", "--dry-run", "--json"])
    assert result.exit_code == 0, result.output
    updated = review.call_args.args[0].to_intent()
    assert (updated.delivery.owner_contact.text, updated.delivery.owner_contact.link) == ("Ask for help", "")
    apply.assert_not_called()


def test_fleet_export_keeps_the_public_contact_link(tmp_home: Path) -> None:
    delivery = DeliveryIntent.model_validate(
        {"owner_contact": {"text": "Ask for help", "link": "https://example.com/help"}}
    )
    ClusterConfig(topology_intent=single_exit_intent(delivery=delivery)).save()
    result = CliRunner().invoke(app, ["fleet", "export", "--json"])
    assert result.exit_code == 0, result.output
    assert (
        json.loads(result.stdout)["data"]["document"]["delivery"]["owner_contact"]["link"] == "https://example.com/help"
    )


def test_fleet_import_reviews_and_preserves_public_contact_values(tmp_home: Path, tmp_path: Path) -> None:
    from meridian import config
    from meridian.servers import ServerRegistry
    from meridian.setup.fleet_document import dump_fleet_document, fleet_document

    current = single_exit_intent()
    ClusterConfig(topology_intent=current).save()
    document = fleet_document(current, ServerRegistry(config.SERVER_PROFILES_FILE))
    document["delivery"]["owner_contact"] = {
        "text": "Open https://example.com/help",
        "link": "https://example.com/help",
    }
    source = tmp_path / "fleet.yml"
    source.write_text(dump_fleet_document(document))
    with (
        patch("meridian.setup.runtime.SetupRuntime.review", return_value=review_result()) as review,
        patch("meridian.setup.runtime.SetupRuntime.apply_intent") as apply,
    ):
        result = CliRunner().invoke(app, ["fleet", "import", str(source), "--dry-run", "--json"])
    assert result.exit_code == 0, result.output
    envelope = json.loads(result.stdout)
    change = next(change for change in envelope["data"]["changes"] if change["field"] == "owner_contact")
    assert "https://example.com/help" in change["after"]
    assert review.call_args.args[0].to_intent().delivery.owner_contact.text == "Open https://example.com/help"
    apply.assert_not_called()
