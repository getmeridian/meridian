"""Saved removal reviews must agree with their declared targets and CLI envelopes."""

import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from meridian.cli import app
from meridian.commands.client_removal import read_removal_proposal
from meridian.core.agent_types import canonical_sha256
from meridian.core.errors import MeridianError
from meridian.core.output import command_envelope
from meridian.core.people_bulk import BulkPreview, ClientBulkResult
from tests.test_client_bulk_commands import isolated_cli_state as isolated_cli_state
from tests.test_client_removal_proposals import removal_cli as removal_cli
from tests.test_control_people_bulk import commit, preview
from tests.test_service_fence import service as service


@pytest.fixture
def bulk_review(removal_cli):
    remote, _ = removal_cli
    review = preview(remote.service, "delete", names=["sample-a"])
    return review, commit(remote.service, review)


def save_document(tmp_path, body):
    path = tmp_path / "removal.json"
    path.write_text(json.dumps(body))
    return str(path)


def seal_preview(body):
    body["preview_hash"] = canonical_sha256(
        BulkPreview.model_validate(body).model_dump(mode="json", exclude={"preview_hash"})
    )


@pytest.mark.parametrize("wrapped", [False, True], ids=["api", "cli"])
@pytest.mark.parametrize("edit", ["hash", "display_name"])
def test_proposal_reader_checks_the_saved_preview_hash(bulk_review, tmp_path, wrapped, edit):
    review, _ = bulk_review
    body = review.to_data()
    if edit == "hash":
        body["preview_hash"] = "e" * 64
    else:
        body["people"][0]["display_name"] = "Another label"
    if wrapped:
        body = command_envelope(
            command="client.bulk.delete",
            data=ClientBulkResult(preview=BulkPreview.model_validate(body), applied=False).to_data(),
        ).to_data()
    with pytest.raises(MeridianError, match="does not match its bulk review"):
        read_removal_proposal(save_document(tmp_path, body))


@pytest.mark.parametrize("shape", ["api_preview", "api_receipt", "cli_preview", "cli_receipt"])
@pytest.mark.parametrize("edit", ["targets", "outcome", "count", "duplicate", "action"])
def test_proposal_reader_checks_targets_and_outcomes_even_with_a_valid_preview_hash(bulk_review, tmp_path, shape, edit):
    review, receipt = bulk_review
    body = (review if shape.endswith("preview") else receipt).to_data()
    if edit == "targets":
        body["proposal"]["usernames"] = ["sample-a", "sample-b"]
    elif edit == "outcome":
        body["people"][0]["outcome"] = "unchanged"
    elif edit == "count":
        body["counts"]["cli_required"] = 0
    elif edit == "duplicate":
        body["people"].append(body["people"][0].copy())
    else:
        body["action"] = {"action": "enable"}
    if shape.endswith("preview"):
        seal_preview(body)
    if shape.startswith("cli"):
        body = command_envelope(
            command="client.bulk.delete" if shape.endswith("preview") else "client.bulk.receipt",
            data={"preview": body, "applied": False}
            if shape.endswith("preview")
            else {"receipt": body, "applied": False},
        ).to_data()
    with pytest.raises(MeridianError, match="does not match its bulk review"):
        read_removal_proposal(save_document(tmp_path, body))


@pytest.mark.parametrize("edit", ["missing_metadata", "extra", "secret", "schema", "metadata_type"])
def test_proposal_reader_validates_the_entire_cli_envelope_before_loading_the_fleet(bulk_review, tmp_path, edit):
    review, _ = bulk_review
    body = command_envelope(
        command="client.bulk.delete", data=ClientBulkResult(preview=review, applied=False).to_data()
    ).to_data()
    if edit == "missing_metadata":
        body = {"data": body["data"]}
    elif edit == "extra":
        body["annotation"] = "not part of the envelope"
    elif edit == "secret":
        body["uuid"] = "sample-private-credential"
    elif edit == "schema":
        body["schema"] = "meridian.output/v2"
    else:
        body["duration_ms"] = "10"
    with patch("meridian.commands._helpers.ClusterConfig.load") as load:
        result = CliRunner().invoke(
            app, ["client", "remove", "--proposal", save_document(tmp_path, body), "--dry-run", "--json"]
        )
    assert result.exit_code == 2, result.output
    assert "sample-private-credential" not in result.output
    load.assert_not_called()


@pytest.mark.parametrize("edit", ["preview_hash", "receipt_hash", "id", "targets"])
def test_proposal_reader_checks_both_halves_of_a_combined_cli_result(bulk_review, tmp_path, edit):
    review, receipt = bulk_review
    body = command_envelope(
        command="client.bulk.delete", data=ClientBulkResult(preview=review, receipt=receipt, applied=True).to_data()
    ).to_data()
    if edit == "preview_hash":
        body["data"]["preview"]["preview_hash"] = "e" * 64
    elif edit == "receipt_hash":
        body["data"]["receipt"]["preview_hash"] = "e" * 64
    elif edit == "id":
        body["data"]["receipt"]["id"] = "e" * 32
    else:
        body["data"]["receipt"]["proposal"]["usernames"] = ["sample-b"]
        body["data"]["receipt"]["people"][0]["username"] = "sample-b"
    with pytest.raises(MeridianError, match="does not match its bulk review"):
        read_removal_proposal(save_document(tmp_path, body))


def test_proposal_reader_accepts_an_unchanged_combined_cli_result(bulk_review, tmp_path):
    review, receipt = bulk_review
    body = command_envelope(
        command="client.bulk.delete", data=ClientBulkResult(preview=review, receipt=receipt, applied=True).to_data()
    ).to_data()
    assert read_removal_proposal(save_document(tmp_path, body)) == review.proposal
