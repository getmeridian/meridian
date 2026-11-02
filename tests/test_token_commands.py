"""CLI token review, explicit permissions, expiry, and producing-output boundaries."""

import io
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from typer.testing import CliRunner

from meridian.cli import app
from meridian.cluster import ClusterConfig, PanelConfig
from meridian.console import set_json_mode, set_quiet_mode
from meridian.control.client import ControlClient
from meridian.control.models import TokenCreate
from meridian.control.tokens import ROOT
from tests.support.control_service import ADMIN_TOKEN
from tests.support.text import unstyle
from tests.support.token_service import Fleet, serve_tokens
from tests.support.topology import single_exit_intent


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


@pytest.mark.parametrize("command", ["create", "revoke"])
@pytest.mark.parametrize("terminal", ["declined", "unavailable"])
def test_declined_or_non_tty_token_mutations_exit_one_without_changing_tokens(
    fleet: Fleet, monkeypatch: pytest.MonkeyPatch, command: str, terminal: str
) -> None:
    from meridian import console

    def tty(*args: object, **kwargs: object) -> io.StringIO:
        if terminal == "unavailable":
            raise OSError("No terminal")
        return io.StringIO("n\n")

    monkeypatch.setattr(console, "open", tty, raising=False)
    if command == "create":
        arguments = ["token", "create", "Reader", "--capability", "people.read"]
    else:
        issued = fleet.service.tokens.issue(TokenCreate(name="Reader", capabilities=["people.read"]), issuer=ROOT)
        arguments = ["token", "revoke", issued.id]
    before = fleet.service.tokens.list_tokens()
    result = CliRunner().invoke(app, arguments)
    assert result.exit_code == 1, result.output
    assert fleet.service.tokens.list_tokens() == before


@pytest.mark.parametrize("scope", [None, "read"])
def test_issuance_uses_the_reviewed_expiry_after_waiting_for_confirmation(
    fleet: Fleet, monkeypatch: pytest.MonkeyPatch, scope: str | None
) -> None:
    from meridian.commands import token

    def confirm(*args: object, **kwargs: object) -> bool:
        fleet.clock.now += 60
        return True

    monkeypatch.setattr(token, "confirm", confirm)
    expiry = fleet.clock.now + 30 * 86400
    grants = ["--scope", scope] if scope else ["--capability", "people.read"]
    result = CliRunner().invoke(app, ["token", "create", "Reader", *grants, "--expires-days", "30"])
    assert result.exit_code == 0, result.output
    issued = fleet.service.tokens.list_tokens().tokens[0]
    from datetime import datetime

    assert datetime.fromisoformat(issued.expires_at).timestamp() == expiry
    assert issued.scope == scope


def test_a_review_that_expires_during_confirmation_refuses_issuance(
    fleet: Fleet, monkeypatch: pytest.MonkeyPatch
) -> None:
    from meridian.commands import token

    def confirm(*args: object, **kwargs: object) -> bool:
        fleet.clock.now += 86400 + 1
        return True

    monkeypatch.setattr(token, "confirm", confirm)
    result = CliRunner().invoke(
        app, ["token", "create", "Reader", "--capability", "people.read", "--expires-days", "1"]
    )
    assert result.exit_code == 2, result.output
    assert fleet.service.tokens.list_tokens().tokens == []


@pytest.mark.parametrize(
    ("url", "credential", "backend"),
    [
        ("", "root-token", "meridian"),
        ("https://198.51.100.10/console", "", "meridian"),
        ("", "", "meridian"),
        ("https://198.51.100.10/console", "root-token", "remnawave"),
        ("", "", "remnawave"),
    ],
    ids=["missing-url", "missing-credential", "unconfigured", "legacy", "legacy-unconfigured"],
)
@pytest.mark.parametrize(
    "command",
    [
        ["list"],
        ["create", "Reader", "--capability", "people.read", "--dry-run"],
        ["revoke", "0123456789ab", "--yes"],
    ],
    ids=["list", "create", "revoke"],
)
def test_token_commands_refuse_missing_or_legacy_service_access_before_building_a_client(
    tmp_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    credential: str,
    backend: str,
    command: list[str],
) -> None:
    from meridian.commands import token

    intent = single_exit_intent()
    intent = type(intent).model_validate(
        {**intent.to_data(), "control": {**intent.control.to_data(), "backend": backend}}
    )
    cluster = ClusterConfig(topology_intent=intent, panel=PanelConfig(url=url, api_token=credential))
    monkeypatch.setattr(token, "load_topology_cluster", lambda: cluster)

    def client(*args: object, **kwargs: object) -> None:
        pytest.fail("A refused fleet must never construct a client.")

    monkeypatch.setattr(token, "ControlClient", client)
    result = CliRunner().invoke(app, ["--lang", "en", "token", *command, "--json"])
    assert result.exit_code == 2, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert payload["command"] == "token." + command[0]
    hint = payload["errors"][0]["hint"]
    if backend == "remnawave":
        assert "meridian migrate" in hint
    else:
        from meridian.i18n import t

        assert hint == t("events.deploy_first")
        assert payload["errors"][0]["message"] == t("events.credentials_missing")
