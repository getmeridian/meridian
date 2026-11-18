"""CLI edits use the authoritative version and never contact the fleet's SSH servers."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from meridian import config
from meridian.cli import app
from meridian.cluster import ClusterConfig
from meridian.console import set_json_mode, set_quiet_mode
from meridian.core.schema import schema_model
from meridian.servers import ServerRegistry
from meridian.setup.fleet_document import dump_fleet_document, fleet_document
from tests.support.presentation import deployed
from tests.support.topology import single_exit_intent


@pytest.fixture(autouse=True)
def output_mode() -> Iterator[None]:
    yield
    set_json_mode(False)
    set_quiet_mode(False)


def run(*args: str, code: int = 0) -> dict:
    result = CliRunner().invoke(app, ["--lang", "en", *args, "--json"])
    assert result.exit_code == code, (result.output, result.exception)
    return json.loads(result.stdout)


def test_contact_edits_review_public_values_and_preserve_local_intent(tmp_home: Path) -> None:
    with deployed(tmp_home) as service:
        before = service.presentation()
        local = ClusterConfig.load().topology_intent
        args = ("fleet", "contact", "--text", "Ask for help", "--link", "HTTPS://EXAMPLE.COM:443/help")
        assert "--yes" in run(*args, code=2)["errors"][0]["message"]
        preview = run(*args, "--dry-run")
        schema_model("fleet-contact-envelope").model_validate(preview)
        assert preview["data"]["owner_contact"] == {"text": "Ask for help", "link": "https://example.com/help"}
        assert service.presentation() == before
        saved = run(*args, "--yes")
        assert saved["data"]["version"] == before.version + 1
        assert run("fleet", "contact")["data"]["owner_contact"] == saved["data"]["owner_contact"]
        assert run("fleet", "contact", "--link", "none", "--yes")["data"]["owner_contact"] == {
            "text": "Ask for help",
            "link": "",
        }
        assert ClusterConfig.load().topology_intent == local


def test_declining_contact_writes_nothing(tmp_home: Path) -> None:
    with (
        deployed(tmp_home) as service,
        patch("meridian.console.has_tty", return_value=True),
        patch("meridian.console.confirm", return_value=False),
    ):
        before = service.presentation()
        result = CliRunner().invoke(app, ["--lang", "en", "fleet", "contact", "--text", "Ask the owner"])
        assert result.exit_code == 1 and "Ask the owner" in result.output
        assert service.presentation() == before


@pytest.mark.parametrize("command,value", [("title", "Household"), ("refresh", "19")])
def test_cli_subscription_fields_round_trip_without_topology(tmp_home: Path, command: str, value: str) -> None:
    with deployed(tmp_home):
        original = run("fleet", command)["data"]["value"]
        assert str(run("fleet", command, value, "--dry-run")["data"]["value"]) == value
        assert run("fleet", command)["data"]["value"] == original
        result = run("fleet", command, value, "--yes")
        assert result["status"] == "changed"
        assert str(run("fleet", command)["data"]["value"]) == value
        assert run("fleet", command, value, "--yes")["status"] == "no_changes"


def test_export_cannot_replay_old_values_and_explicit_legacy_import_reviews_live_state(tmp_home: Path) -> None:
    current = single_exit_intent()
    with deployed(tmp_home, current) as service:
        run("fleet", "title", "Newer title", "--yes")
        export = run("fleet", "export")["data"]["document"]
        assert not set(export["delivery"]) & {"profile_title", "update_interval_hours", "owner_contact"}
        assert "server_metadata" not in export
        doc = fleet_document(current, ServerRegistry(config.SERVER_PROFILES_FILE))
        doc["delivery"]["profile_title"] = "Imported title"
        doc["delivery"]["owner_contact"] = {"text": "Help", "link": "mailto:help@example.com"}
        source = tmp_home / "fleet.yml"
        source.write_text(dump_fleet_document(doc))
        args = ("fleet", "import", str(source))
        review = run(*args, "--dry-run")
        diff = next(row for row in review["data"]["changes"] if row["field"] == "profile_title")
        assert (diff["before"], diff["after"]) == ("Newer title", "Imported title")
        assert service.presentation().profile_title == "Newer title"
        assert "--yes" in run(*args, code=2)["errors"][0]["message"]
        doc["delivery"]["client_routing"] = "full"
        source.write_text(dump_fleet_document(doc))
        assert "both topology" in run(*args, "--yes", code=2)["errors"][0]["message"]
        assert service.presentation().profile_title == "Newer title"
        doc["delivery"].pop("client_routing")
        source.write_text(dump_fleet_document(doc))
        assert run(*args, "--yes")["status"] == "changed"
        assert service.presentation().owner_contact.text == "Help"
        assert ClusterConfig.load().topology_intent == current
