"""Opt-in SSH key setup for servers that accept only a password (CLI deploy and setup)."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import typer

from meridian.commands.resolve import ensure_server_connection
from meridian.commands.server import run_add
from meridian.console import set_json_mode, set_quiet_mode
from meridian.resolve import ResolvedServer
from meridian.servers import ServerRegistry
from meridian.ssh import ServerConnection, SSHError, SSHPasswordOnlyError, offers_password_login
from meridian.ssh_keys import KeyMaterial

PASSWORD = "one-time-s3cret-value"
HOST = "198.51.100.10"
PASSWORD_ONLY = f"root@{HOST}: Permission denied (publickey,password)."


def _done(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class FakeSSH:
    """The ssh binary for a server that takes PASSWORD, then accepts the installed key."""

    def __init__(self, *, key_login: bool = True) -> None:
        self.key_login = key_login
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        env = kwargs.get("env") or {}
        self.calls.append((cmd, env))
        if "MERIDIAN_SSH_PASSWORD" in env:
            if env["MERIDIAN_SSH_PASSWORD"] == PASSWORD:
                return _done()
            return _done(255, stderr=f"Permission denied, please try again.\n{PASSWORD_ONLY}")
        if "-i" in cmd and self.key_login:
            return _done(stdout="ok\n")
        return _done(255, stderr=PASSWORD_ONLY)

    @property
    def password_calls(self) -> list[list[str]]:
        return [cmd for cmd, env in self.calls if "MERIDIAN_SSH_PASSWORD" in env]


@pytest.fixture(autouse=True)
def _human_mode() -> Any:
    set_json_mode(False)
    set_quiet_mode(False)
    yield
    set_json_mode(False)


@pytest.fixture
def key(tmp_home: Path) -> KeyMaterial:
    return KeyMaterial(
        private_key_path=str(tmp_home / "ssh" / "meridian_ed25519"),
        public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAItest meridian",
    )


def _resolved(user: str = "root") -> ResolvedServer:
    return ResolvedServer(ip=HOST, user=user, local_mode=False, conn=ServerConnection(HOST, user, multiplex=False))


def _terminal(*, tty: bool = True, accept: bool = True, password: str = PASSWORD) -> dict[str, MagicMock]:
    return {
        "has_tty": MagicMock(return_value=tty),
        "confirm": MagicMock(return_value=accept),
        "prompt_secret": MagicMock(return_value=password),
    }


@contextmanager
def _patched(fake: FakeSSH, key: KeyMaterial, terminal: dict[str, MagicMock]) -> Iterator[None]:
    """A host whose key is trusted, the fake ssh binary, Meridian's key, and the operator's answers."""
    with ExitStack() as stack:
        stack.enter_context(patch("meridian.ssh.subprocess.run", side_effect=fake))
        stack.enter_context(patch("meridian.ssh._host_key_known", return_value=True))
        stack.enter_context(patch("meridian.resolve.host_key_known", return_value=True))
        stack.enter_context(patch("meridian.resolve.ensure_meridian_keypair", return_value=key))
        for name, mock in terminal.items():
            stack.enter_context(patch(f"meridian.commands.resolve.{name}", new=mock))
        yield


def _connect(fake: FakeSSH, key: KeyMaterial, terminal: dict[str, MagicMock], **kwargs: Any) -> ResolvedServer:
    with _patched(fake, key, terminal):
        return ensure_server_connection(_resolved(), **kwargs)


@pytest.mark.parametrize(
    ("stderr", "offers"),
    [
        (PASSWORD_ONLY, True),
        (f"root@{HOST}: Permission denied (publickey,keyboard-interactive).", True),
        (f"root@{HOST}: Permission denied (publickey).", False),
        ("ssh: connect to host 198.51.100.10 port 22: Connection refused", False),
    ],
    ids=["password", "keyboard-interactive", "key-only", "refused"],
)
def test_password_only_is_read_from_the_servers_auth_methods(stderr: str, offers: bool) -> None:
    assert offers_password_login(stderr) is offers


@patch("meridian.ssh._host_key_known", return_value=True)
def test_password_only_server_raises_a_user_error_with_the_ssh_copy_id_hint(_known: Any) -> None:
    conn = ServerConnection(HOST, "ubuntu", port=2222, multiplex=False)
    with (
        patch.object(conn, "run", return_value=_done(255, stderr=PASSWORD_ONLY)),
        pytest.raises(SSHPasswordOnlyError) as exc,
    ):
        conn.check_ssh()

    assert exc.value.category == "user"
    assert f"ssh-copy-id -p 2222 ubuntu@{HOST}" in exc.value.hint


def test_accepting_the_offer_installs_the_key_once_and_never_leaks_the_password(
    key: KeyMaterial,
    tmp_home: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeSSH()
    terminal = _terminal()

    with caplog.at_level(logging.DEBUG, logger="meridian"):
        keyed = _connect(fake, key, terminal, offer_key_setup=True)

    assert keyed.conn.identity_file == key.private_key_path
    assert terminal["confirm"].call_args.kwargs == {"default": False}
    # The password rides one install command, as the login user, through askpass only.
    (install,) = fake.password_calls
    assert "authorized_keys" in install[-1] and "sudo" not in install[-1]
    assert "BatchMode=no" in install
    # Key login and the deploy's own SSH check both use the new key.
    assert all("-i" in cmd for cmd, env in fake.calls[2:] if "MERIDIAN_SSH_PASSWORD" not in env)
    captured = capsys.readouterr()
    assert PASSWORD not in captured.out + captured.err
    assert PASSWORD not in caplog.text
    assert all(PASSWORD not in " ".join(cmd) for cmd, _env in fake.calls)
    assert all(PASSWORD not in path.read_text(errors="ignore") for path in tmp_home.rglob("*") if path.is_file())


def test_declining_the_offer_fails_with_the_manual_hint_and_never_asks_for_a_password(
    key: KeyMaterial, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeSSH()
    terminal = _terminal(accept=False)

    with pytest.raises(typer.Exit) as exc:
        _connect(fake, key, terminal, offer_key_setup=True)

    assert exc.value.exit_code == 2
    terminal["prompt_secret"].assert_not_called()
    assert fake.password_calls == []
    assert "ssh-copy-id" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("tty", "json_mode", "offer"),
    [(False, False, True), (True, True, True), (True, False, False)],
    ids=["no-terminal", "json", "yes-flag"],
)
def test_headless_runs_never_prompt_and_fail_with_the_hint(
    key: KeyMaterial, capsys: pytest.CaptureFixture[str], tty: bool, json_mode: bool, offer: bool
) -> None:
    fake = FakeSSH()
    terminal = _terminal(tty=tty)
    set_json_mode(json_mode)

    with pytest.raises(typer.Exit) as exc:
        _connect(fake, key, terminal, offer_key_setup=offer)

    assert exc.value.exit_code == 2
    terminal["confirm"].assert_not_called()
    terminal["prompt_secret"].assert_not_called()
    assert fake.password_calls == []
    captured = capsys.readouterr()
    assert "ssh-copy-id" in captured.out + captured.err


def test_wrong_password_fails_without_echoing_it(key: KeyMaterial, capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeSSH()
    wrong = "not-the-right-one"

    with pytest.raises(typer.Exit) as exc:
        _connect(fake, key, _terminal(password=wrong), offer_key_setup=True)

    assert exc.value.exit_code == 2
    err = capsys.readouterr().err
    assert "Could not install Meridian's SSH key" in err
    assert wrong not in err


def test_a_key_that_still_cannot_log_in_is_reported(key: KeyMaterial, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(typer.Exit) as exc:
        _connect(FakeSSH(key_login=False), key, _terminal(), offer_key_setup=True)

    assert exc.value.exit_code == 3
    assert "PubkeyAuthentication" in capsys.readouterr().err


def test_password_is_never_sent_to_an_unverified_host(key: KeyMaterial) -> None:
    from meridian.resolve import install_meridian_key

    with (
        patch("meridian.resolve.host_key_known", return_value=False),
        patch("meridian.ssh.subprocess.run") as ssh,
        pytest.raises(SSHError, match="not trusted"),
    ):
        install_meridian_key(_resolved(), PASSWORD)

    ssh.assert_not_called()


def test_setup_saves_the_installed_key_in_the_server_profile(key: KeyMaterial, servers_file: Path) -> None:
    with _patched(FakeSSH(), key, _terminal()), patch("meridian.config.SERVER_PROFILES_FILE", servers_file):
        run_add(HOST, name="edge", offer_key_setup=True)

    entry = ServerRegistry(servers_file).find("edge")
    assert entry is not None and entry.key_path == key.private_key_path
    assert entry.auth_state == "validated"
    assert PASSWORD not in servers_file.read_text()
