"""Exercise upgrade command boundaries and cleanup without a provider or network."""

from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from packaging.version import Version

from meridian.control.schema import SCHEMA_VERSION
from meridian.control_artifacts import AgentBinary
from meridian.core.client_limits import parse_traffic_limit
from meridian.core.clients import ClientDetail
from meridian.host_runtime import CONTROL_SERVICE_STATE_DIR
from meridian.servers import ServerEntry, ServerRegistry
from meridian.ssh import CommandResult
from tests.realvm import upgrade_verifier as verifier

IP = "198.51.100.10"


def test_automatic_version_selection_reports_unreachable_pypi_as_inconclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verifier, "get_pypi_releases", lambda: None)
    with pytest.raises(verifier.VerificationError) as exc:
        verifier.select_previous_version("4.0.0b10", {})
    assert exc.value.exit_code == 75


def test_an_explicit_version_never_fetches_pypi_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verifier, "get_pypi_releases", lambda: pytest.fail("PyPI was reached"))
    assert verifier.select_previous_version("4.0.0b10", {"REALVM_UPGRADE_FROM": "4.0.0b10"}) == "4.0.0b10"


def test_no_previous_published_release_is_a_refusal_not_an_external_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verifier, "get_pypi_releases", lambda: [Version("4.0.0b10")])
    with pytest.raises(verifier.VerificationError) as exc:
        verifier.select_previous_version("4.0.0b10", {})
    assert exc.value.exit_code == 2


@pytest.mark.parametrize("problem", ["wrong-checkout", "missing-version"])
def test_upgrade_preflight_refuses_false_checkout_or_agent_evidence_before_pypi(
    problem: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        verifier, "source_checkout", lambda: tmp_path / "other" if problem == "wrong-checkout" else tmp_path
    )
    monkeypatch.setattr(verifier, "agent_binary", lambda _arch: AgentBinary(data=b"binary", sha256="", version=""))
    monkeypatch.setattr(verifier, "get_pypi_releases", lambda: pytest.fail("PyPI was reached"))
    with pytest.raises(verifier.VerificationError) as exc:
        verifier.preflight_upgrade(tmp_path, {})
    assert exc.value.exit_code == 2


@pytest.mark.parametrize("yes", [False, True])
def test_client_set_uses_only_options_the_old_release_advertises(
    yes: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = verifier.UpgradeVerifier(tmp_path, tmp_path / "work", IP, {"MERIDIAN_HOME": str(tmp_path / "home")})
    calls: list[tuple[list[str], str | None]] = []
    help_text = "--limit TEXT --expires TEXT" + (" --yes" if yes else "")

    def execute(_label: str, args: list[str], **kwargs: object) -> CommandResult:
        calls.append((args, kwargs.get("stdin")))  # type: ignore[arg-type]
        return CommandResult(args=args, returncode=0, stdout=help_text if "--help" in args else "")

    monkeypatch.setattr(run, "execute", execute)
    run.set_limited_client("2030-01-01")

    args, stdin = calls[-1]
    assert (
        args[-5:] == ["--limit", "50GB", "--expires", "2030-01-01", "--yes"]
        if yes
        else args[-4:] == ["--limit", "50GB", "--expires", "2030-01-01"]
    )
    assert stdin == (None if yes else "y\n")


def test_an_old_release_without_expiry_options_is_refused_with_a_specific_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = verifier.UpgradeVerifier(tmp_path, tmp_path / "work", IP, {})
    monkeypatch.setattr(
        run, "execute", lambda *args, **kwargs: CommandResult(args=[], returncode=0, stdout="--limit TEXT")
    )
    with pytest.raises(verifier.VerificationError, match="expiry options") as exc:
        run.set_limited_client("2030-01-01")
    assert exc.value.exit_code == 2


def test_the_old_release_cannot_inherit_the_checkout_agent_or_python_path(tmp_path: Path) -> None:
    run = verifier.UpgradeVerifier(
        tmp_path,
        tmp_path / "work",
        IP,
        {
            "MERIDIAN_HOME": str(tmp_path / "home"),
            "MERIDIAN_AGENT_BINARY": "/tmp/netagent",
            "PYTHONPATH": "/tmp/checkout/src",
            "MERIDIAN_CONTEXT": "production",
            "HCLOUD_TOKEN": "provider-token",
        },
    )
    assert "MERIDIAN_AGENT_BINARY" not in run.old_env
    assert "PYTHONPATH" not in run.old_env
    assert "MERIDIAN_CONTEXT" not in run.old_env
    assert "HCLOUD_TOKEN" not in run.old_env
    assert run.old_env["MERIDIAN_HOME"] == run.env["MERIDIAN_HOME"]
    assert run.env["MERIDIAN_AGENT_BINARY"] == "/tmp/netagent"


@pytest.mark.parametrize(
    ("output", "certificates", "installation", "code"),
    [
        ("urn:ietf:params:acme:error:rateLimited", True, False, 75),
        ("Failed to fetch https://pypi.org/simple/meridian-vpn/: connection refused", False, True, 75),
        ("No solution found: meridian-vpn==4.0.0b10 does not exist", False, True, 2),
        ("The service did not start", False, False, 1),
    ],
    ids=["certificate-limit", "pypi-transport", "missing-version", "product-failure"],
)
def test_command_failures_keep_external_conditions_separate_and_do_not_print_raw_output(
    output: str,
    certificates: bool,
    installation: bool,
    code: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run = verifier.UpgradeVerifier(tmp_path, tmp_path / "work", IP, {"MERIDIAN_HOME": str(tmp_path / "home")})
    raw = output + f"\nhttps://{IP}/sub/secret-token\n"
    monkeypatch.setattr(
        verifier.subprocess, "run", lambda *args, **kwargs: CommandResult(args=[], returncode=1, stderr=raw)
    )

    with pytest.raises(verifier.VerificationError) as exc:
        run.execute("apply", ["fake"], certificates=certificates, installation=installation)

    assert exc.value.exit_code == code
    text = str(exc.value) + capsys.readouterr().out
    assert IP not in text
    assert "secret-token" not in text


def test_a_certificate_rate_limit_is_inconclusive_even_when_setup_used_a_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = verifier.UpgradeVerifier(tmp_path, tmp_path / "work", IP, {})
    monkeypatch.setattr(
        verifier.subprocess,
        "run",
        lambda *args, **kwargs: CommandResult(
            args=[], returncode=0, stdout="refused a new certificate; using self-signed fallback"
        ),
    )
    with pytest.raises(verifier.VerificationError) as exc:
        run.execute("setup", ["fake"], certificates=True)
    assert exc.value.exit_code == 75


def test_schema_check_reads_the_runtime_database_in_read_only_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = verifier.UpgradeVerifier(tmp_path, tmp_path / "work", IP, {})
    commands: list[list[str]] = []

    def execute(_label: str, args: list[str], **kwargs: object) -> CommandResult:
        commands.append(args)
        return CommandResult(args=args, returncode=0, stdout=str(SCHEMA_VERSION))

    monkeypatch.setattr(run, "execute", execute)
    run.verify_schema()
    assert f"{CONTROL_SERVICE_STATE_DIR}/control.db" in commands[0][-1]
    assert "mode=ro" in commands[0][-1]
    assert "schema_version" in commands[0][-1]


@pytest.mark.parametrize("code", [0, 1, 75])
def test_verifier_removes_its_previous_venv_and_captures_on_every_verdict(
    code: int, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TARGET_IP", IP)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    created: list[Path] = []

    def run(self: verifier.UpgradeVerifier) -> None:
        created.append(self.work)
        (self.work / "secret-capture").write_text("subscription-token")
        if code:
            raise verifier.VerificationError("Run did not conclude" if code == 75 else "Upgrade failed", exit_code=code)

    monkeypatch.setattr(verifier.UpgradeVerifier, "run", run)
    assert verifier.main() == code
    assert created and not created[0].exists()
    assert IP not in capsys.readouterr().out


def test_standalone_verifier_refuses_ci_before_any_network_or_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TARGET_IP", IP)
    monkeypatch.setenv("CI", "true")
    monkeypatch.setattr(verifier.UpgradeVerifier, "run", lambda _self: pytest.fail("the verifier started"))
    assert verifier.main() == 2


def test_capture_keeps_every_user_agent_even_when_two_select_the_same_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = verifier.UpgradeVerifier(tmp_path, tmp_path / "work", IP, {})
    requested: list[str] = []

    def response(request: httpx.Request) -> httpx.Response:
        agent = request.headers["User-Agent"]
        requested.append(agent)
        return httpx.Response(
            200, content=agent.encode(), headers={"subscription-userinfo": "upload=0; download=0; total=0; expire=0"}
        )

    client = httpx.Client(transport=httpx.MockTransport(response))
    monkeypatch.setattr(verifier.httpx, "Client", lambda **kwargs: client)
    captured = run.capture({"bob": f"https://{IP}/sub/opaque-token"}, "before")

    assert len(requested) == len(set(requested)) == len(captured) == 5
    assert {value.body.decode() for value in captured.values()} == set(requested)
    assert any("xray-json" in label and "v2rayNG" in label for _person, label in captured)
    assert any("xray-json" in label and "Happ" in label for _person, label in captured)


@pytest.mark.parametrize("change", ["", "document", "limit", "schema", "agent"])
def test_the_complete_upgrade_journey_uses_old_commands_then_checks_every_upgrade_claim(
    change: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "fleet" / "home"
    run = verifier.UpgradeVerifier(tmp_path, tmp_path / "captures", IP, {"MERIDIAN_HOME": str(home)})
    monkeypatch.setattr(verifier, "preflight_upgrade", lambda _root, _env: ("4.0.0b10", "0.2.0"))

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 2, 12, tzinfo=UTC)

    monkeypatch.setattr(verifier, "datetime", Clock)
    ticks = itertools.count(step=10)
    monkeypatch.setattr(verifier.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(verifier.time, "sleep", lambda _seconds: None)
    upgraded = False
    events: list[str] = []

    def execute(args: list[str], **kwargs: object) -> CommandResult:
        nonlocal upgraded
        old = args[0] == run.previous[0]
        command = args[1:] if old else args[3:] if args[:3] == run.current else args
        events.append(("old " if old else "new ") + " ".join(command))
        stdout = ""
        if args[0] == "ssh-keyscan":
            stdout = f"{IP} ssh-ed25519 dummy-key\n"
        elif args[0] == "ssh":
            stdout = str(0 if change == "schema" else SCHEMA_VERSION)
        elif command == ["--version"]:
            stdout = "meridian 4.0.0b10"
        elif command[:2] == ["server", "add"]:
            ServerRegistry(home / "servers.json").add(ServerEntry(host=IP))
        elif command == ["client", "set", "--help"]:
            stdout = "--limit TEXT --expires TEXT"
        elif command[:2] == ["client", "set"]:
            assert kwargs["input"] == "y\n"
            assert "--yes" not in command
        elif command[:1] == ["apply"]:
            assert not old
            upgraded = True
        elif command[:1] == ["--json"]:
            leaf = command[1:]
            data: dict[str, object] = {}
            if leaf == ["client", "list"]:
                data = {"clients": [{"username": "default"}, {"username": "bob"}]}
            elif leaf[:2] == ["client", "show"]:
                name = leaf[2]
                person = ClientDetail(
                    username=name,
                    id="person-1",
                    status="active",
                    traffic_used_bytes=46,
                    traffic_limit_bytes=parse_traffic_limit("50GB") if name == "bob" else 0,
                    traffic_reset="never",
                    expires_at="2026-12-02T23:59:59Z" if name == "bob" else "",
                    created_at="2026-11-02T12:00:00Z",
                    last_seen="",
                    lifetime_traffic_bytes=46,
                )
                if upgraded and change == "limit" and name == "bob":
                    person = person.model_copy(update={"traffic_limit_bytes": 0})
                data = {
                    "client": person.model_dump(mode="json"),
                    "handoff": {"subscription_url": f"https://{IP}/sub/{name}-opaque-token"},
                }
            elif leaf == ["plan"]:
                data = {"converged": upgraded, "exit_code": 0 if upgraded else 2}
            elif leaf == ["fleet", "status"]:
                data = {
                    "nodes": [
                        {
                            "status": "connected",
                            "runtime_available": True,
                            "runtime": {"versions": {"agent": "0.1.0" if change == "agent" else "0.2.0"}},
                        }
                    ]
                }
            elif leaf == ["test"]:
                assert upgraded
                stdout = json.dumps({"summary": {"counts": {"passed": 3, "failed": 0, "warnings": 0, "skipped": 0}}})
            if not stdout:
                stdout = json.dumps({"data": data})
        return CommandResult(args=args, returncode=0, stdout=stdout)

    monkeypatch.setattr(verifier.subprocess, "run", execute)

    def response(request: httpx.Request) -> httpx.Response:
        person = "bob" if "bob-" in request.url.path else "default"
        events.append(f"{'after' if upgraded else 'before'} HTTP {person} {request.headers['User-Agent']}")
        body = b"opaque subscription document"
        if (
            upgraded
            and change == "document"
            and person == "bob"
            and request.headers["User-Agent"].startswith("clash-verge")
        ):
            body += b" changed"
        limit = parse_traffic_limit("50GB") if person == "bob" else 0
        expiry = int(datetime(2026, 12, 2, 23, 59, 59, tzinfo=UTC).timestamp()) if person == "bob" else 0
        return httpx.Response(
            200,
            content=body,
            headers={"subscription-userinfo": f"upload=12; download=34; total={limit}; expire={expiry}"},
        )

    client_type = httpx.Client
    monkeypatch.setattr(
        verifier.httpx, "Client", lambda **kwargs: client_type(transport=httpx.MockTransport(response), **kwargs)
    )
    if change:
        with pytest.raises(verifier.VerificationError) as exc:
            run.run()
        assert exc.value.exit_code == 1
        assert "opaque subscription document" not in str(exc.value)
        assert IP not in str(exc.value)
        if change == "document":
            assert "bob / mihomo (clash-verge)" in str(exc.value)
            assert "first difference" in str(exc.value)
    else:
        run.run()
        assert run.passed == 10
        assert len([event for event in events if event.startswith("before HTTP")]) == 10
        assert len([event for event in events if event.startswith("after HTTP")]) == 10
        assert events[-1] == "new --json test"
        apply = events.index("new apply --yes")
        assert all(index < apply for index, event in enumerate(events) if event.startswith("before HTTP"))
        assert all(index > apply for index, event in enumerate(events) if event.startswith("after HTTP"))
    output = capsys.readouterr().out
    assert IP not in output
    assert "opaque-token" not in output
