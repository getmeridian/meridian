"""CLI token review, explicit permissions, expiry, and producing-output boundaries."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from typer.testing import CliRunner

from meridian.cli import app
from meridian.console import set_json_mode, set_quiet_mode
from meridian.control.client import ControlClient
from tests.support.control_service import ADMIN_TOKEN
from tests.support.text import unstyle
from tests.support.token_service import Fleet, serve_tokens


@pytest.fixture(autouse=True)
def reset_console_mode() -> Iterator[None]:
    set_json_mode(False)
    set_quiet_mode(False)
    yield
    set_json_mode(False)
    set_quiet_mode(False)


@pytest.fixture
def fleet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Fleet]:
    from meridian.commands import token

    with serve_tokens(tmp_path) as result:

        @contextmanager
        def client() -> Iterator[ControlClient]:
            with ControlClient(result.url, ADMIN_TOKEN, report_mutations=False) as instance:
                yield instance

        monkeypatch.setattr(token, "token_client", client)
        yield result


def test_capability_catalog_is_available_without_a_fleet(tmp_home: Path) -> None:
    result = CliRunner().invoke(app, ["token", "capabilities", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert any(row["capability"] == "people.read" for row in data["capabilities"])


def test_json_preview_returns_effective_words_and_creates_no_token(fleet: Fleet) -> None:
    result = CliRunner().invoke(
        app, ["token", "create", "Reader", "--capability", "people.read", "--dry-run", "--json"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert data["issued"] is None
    assert data["preview"]["capabilities"] == ["people.read"]
    assert data["preview"]["permissions"][0]["labels"]["ru"]
    assert fleet.service.tokens.list_tokens().tokens == []


def test_only_issuance_reveals_the_secret_and_listing_keeps_metadata(fleet: Fleet) -> None:
    runner = CliRunner()
    issued = runner.invoke(app, ["token", "create", "Reader", "--capability", "people.read", "--yes", "--json"])
    assert issued.exit_code == 0, issued.output
    token = json.loads(issued.stdout)["data"]["issued"]["token"]
    assert token.startswith("mrd_")
    assert fleet.service.tokens.authenticate(token).capabilities == {"people.read"}
    listing = runner.invoke(app, ["token", "list", "--json"])
    assert listing.exit_code == 0, listing.output
    assert token not in listing.stdout
    assert json.loads(listing.stdout)["data"]["tokens"][0]["capabilities"] == ["people.read"]


@pytest.mark.parametrize("language", ["en", "ru", "fa", "zh"])
def test_human_review_names_the_effective_permissions_in_each_language(fleet: Fleet, language: str) -> None:
    from meridian.console import set_json_mode

    set_json_mode(False)
    result = CliRunner().invoke(
        app, ["--lang", language, "token", "create", "Reader", "--capability", "people.read", "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    from meridian.control.token_capabilities import permission

    assert permission("people.read").labels[language] in unstyle(result.output)


def test_json_issuance_requires_yes_and_invalid_grants_create_nothing(fleet: Fleet) -> None:
    for flags in [["--capability", "people.read", "--json"], ["--capability", "*", "--yes", "--json"]]:
        result = CliRunner().invoke(app, ["token", "create", "Reader", *flags])
        assert result.exit_code == 2
    assert fleet.service.tokens.list_tokens().tokens == []


def test_no_expiry_is_an_explicit_choice_and_revocation_uses_the_public_id(fleet: Fleet) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app, ["token", "create", "Reader", "--capability", "people.read", "--never-expires", "--yes", "--json"]
    )
    assert result.exit_code == 0, result.output
    token = json.loads(result.stdout)["data"]["issued"]
    assert token["expires_at"] is None
    revoked = runner.invoke(app, ["token", "revoke", token["id"], "--yes", "--json"])
    assert revoked.exit_code == 0, revoked.output
    assert json.loads(revoked.stdout)["data"]["revoked_at"] is not None
