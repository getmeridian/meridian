"""Saved removal reviews must agree with their declared targets and CLI envelopes."""

import json
import os
import threading
from pathlib import Path
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


@pytest.mark.parametrize("source", ["fifo", "descriptor"])
def test_proposal_reader_bounds_streams_with_no_reported_file_size(bulk_review, tmp_path, monkeypatch, source):
    review, _ = bulk_review
    maximum = 1024
    monkeypatch.setattr("meridian.commands.client_removal.MAX_PROPOSAL_FILE_BYTES", maximum)
    payload = review.proposal.model_dump_json(by_alias=True).encode("utf-8") + b" " * (maximum * 2)
    read_fd = None
    if source == "fifo":
        path = tmp_path / "removal.fifo"
        os.mkfifo(path)
    else:
        read_fd, write_fd = os.pipe()
        path = Path(f"/dev/fd/{read_fd}")
    assert path.stat().st_size == 0
    errors = []
    reader_ready = threading.Event()
    original_open = Path.open

    def open_reader(target, mode="r", *args, **kwargs):
        if target == path and mode in {"r", "rb"}:
            reader_ready.set()
        return original_open(target, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_reader)

    def write():
        try:
            if not reader_ready.wait(timeout=5):
                errors.append(RuntimeError("The reader did not open the stream."))
                return
            with path.open("wb") if source == "fifo" else os.fdopen(write_fd, "wb") as stream:
                stream.write(payload)
        except OSError as exc:
            errors.append(exc)

    writer = threading.Thread(target=write, daemon=True)
    writer.start()
    try:
        with pytest.raises(MeridianError, match="valid removal proposal"):
            read_removal_proposal(str(path))
    finally:
        writer.join(timeout=5)
        if read_fd is not None:
            os.close(read_fd)
    assert not writer.is_alive()
    assert not errors


def test_proposal_reader_bounds_a_file_that_grows_when_opened(bulk_review, tmp_path, monkeypatch):
    review, _ = bulk_review
    path = tmp_path / "growing.json"
    path.write_text(review.proposal.model_dump_json(by_alias=True))
    maximum = 1024
    assert path.stat().st_size < maximum
    monkeypatch.setattr("meridian.commands.client_removal.MAX_PROPOSAL_FILE_BYTES", maximum)
    original_open = Path.open
    grown = False

    def open_after_growth(source, mode="r", *args, **kwargs):
        nonlocal grown
        if source == path and mode in {"r", "rb"} and not grown:
            grown = True
            with original_open(path, "ab") as stream:
                stream.write(b" " * (maximum * 2))
        return original_open(source, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_after_growth)
    with pytest.raises(MeridianError, match="valid removal proposal"):
        read_removal_proposal(str(path))
    assert grown


@pytest.mark.parametrize("excess", [0, 1], ids=["at_limit", "over_limit"])
def test_proposal_reader_keeps_the_exact_byte_limit(bulk_review, tmp_path, monkeypatch, excess):
    review, _ = bulk_review
    payload = review.proposal.model_dump_json(by_alias=True).encode("utf-8")
    maximum = len(payload)
    monkeypatch.setattr("meridian.commands.client_removal.MAX_PROPOSAL_FILE_BYTES", maximum)
    path = tmp_path / "removal.json"
    path.write_bytes(payload + b" " * excess)
    if excess:
        with pytest.raises(MeridianError, match="valid removal proposal"):
            read_removal_proposal(str(path))
    else:
        assert read_removal_proposal(str(path)) == review.proposal
