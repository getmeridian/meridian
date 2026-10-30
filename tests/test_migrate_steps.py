"""The server-side steps of `meridian migrate`: backup, install beside the panel, import over SSH, and removal."""

from __future__ import annotations

import base64
import hashlib
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest

from meridian.cluster import ClusterConfig, PanelConfig
from meridian.control.client import ControlClient
from meridian.control.formats.entries import PLACEHOLDER_ADDRESS, PLACEHOLDER_PORT, PLACEHOLDER_UUID
from meridian.control.models import UserImport, UserImportBatch
from meridian.host_runtime import LEGACY_NODE_DIR, NODE_DIR
from meridian.migrate import MigrationError, service_import
from meridian.migrate.backup import SERVER_BACKUP_ROOT, backup_panel
from meridian.migrate.cutover import remove_node_runtime, remove_panel, stop_panel
from meridian.migrate.service_import import MIGRATION_SERVICE_PORT, import_over_ssh, install_beside_panel
from meridian.provision.steps import ProvisionContext, StepResult
from meridian.provision.uninstall import Uninstall, panel_stack_removal
from tests.support.control_service import ADMIN_TOKEN, serve_control
from tests.support.mock_connection import MockConnection
from tests.systemlab.subscription_client import parse_base64_subscription

DUMP = b"PGDMP fake custom-format dump"
ALICE = UserImport(username="alice", uuid="3f2a9c4e-7b1d-4e6a-9c2f-1a2b3c4d5e6f", sub_token="Zq8xT3vNw2LmK7pR")


class CurlOnServer(MockConnection):
    """Runs the import's curl for real: the config it wrote, the body on stdin, against a loopback service."""

    def __init__(self, service_url: str) -> None:
        super().__init__()
        self.service_url = service_url
        self.files: dict[str, str] = {}

    def put_text(self, path: str, text: str, timeout: int = 30, **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.files[path] = text
        return super().put_text(path, text, timeout=timeout, **kwargs)

    def run(self, command: str, timeout: int = 30, **kwargs: object) -> subprocess.CompletedProcess[str]:
        if not command.startswith("curl "):
            return super().run(command, timeout=timeout, **kwargs)
        self._calls.append(command)
        self._run_kwargs.append(kwargs)
        (config,) = self.files.values()
        url = re.search(r'url = "([^"]+)"', config).group(1)  # type: ignore[union-attr]
        assert url.startswith(f"http://127.0.0.1:{MIGRATION_SERVICE_PORT}/")
        headers = dict(line.split(": ", 1) for line in re.findall(r'header = "([^"]+)"', config))
        response = httpx.post(
            url.replace(f"http://127.0.0.1:{MIGRATION_SERVICE_PORT}", self.service_url),
            content=str(kwargs["input"]),
            headers=headers,
        )
        return subprocess.CompletedProcess(command, 0, f"{response.text}\n{response.status_code}", "")


@pytest.fixture
def service_url(tmp_path: Path) -> Iterator[str]:
    with serve_control(tmp_path) as url:
        yield url


def test_the_import_reaches_the_service_from_the_control_server_and_leaves_no_token_behind(service_url: str) -> None:
    conn = CurlOnServer(service_url)

    first = import_over_ssh(conn, UserImportBatch(users=[ALICE]), ADMIN_TOKEN)
    again = import_over_ssh(conn, UserImportBatch(users=[ALICE]), ADMIN_TOKEN)

    assert (first.created, again.unchanged) == (["alice"], ["alice"])
    path, _content, options = conn.writes[0]
    assert (options["mode"], options["sensitive"]) == ("600", True)
    curls = [(call, kwargs) for call, kwargs in zip(conn.calls, conn.run_kwargs) if call.startswith("curl ")]
    assert len(curls) == 2 and all(f"rm -f {path}" in call and kwargs["sensitive"] for call, kwargs in curls)
    assert all(ADMIN_TOKEN not in call for call, _ in curls), "the token never rides a command line"
    with ControlClient(service_url, ADMIN_TOKEN) as client:
        links = parse_base64_subscription(client.fetch_subscription(ALICE.sub_token))
        assert all(
            urlsplit(link).username == PLACEHOLDER_UUID
            and urlsplit(link).hostname == PLACEHOLDER_ADDRESS
            and urlsplit(link).port == PLACEHOLDER_PORT
            for link in links
        )


def test_a_disagreeing_import_is_a_user_error_with_the_services_reason(service_url: str) -> None:
    conn = CurlOnServer(service_url)
    import_over_ssh(conn, UserImportBatch(users=[ALICE]), ADMIN_TOKEN)
    other = ALICE.model_copy(update={"sub_token": "hX4_n-Q9rT2wYb7K"})

    with pytest.raises(MigrationError) as error:
        import_over_ssh(conn, UserImportBatch(users=[other]), ADMIN_TOKEN)

    assert error.value.category == "user" and "all or nothing" in error.value.hint


def test_a_wrong_token_is_refused_as_a_system_error(service_url: str) -> None:
    with pytest.raises(MigrationError) as error:
        import_over_ssh(CurlOnServer(service_url), UserImportBatch(users=[ALICE]), "not-the-token")

    assert error.value.category == "system"


class _Install:
    runs: list[Any] = []

    def __init__(self, install: Any) -> None:
        self.install = install

    def run(self, _conn: Any, _ctx: ProvisionContext) -> StepResult:
        _Install.runs.append(self.install)
        return StepResult(name="install", status="changed")


@pytest.fixture
def no_install(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    _Install.runs = []
    monkeypatch.setattr(service_import, "InstallControlService", _Install)
    monkeypatch.setattr(service_import, "service_package", lambda: "meridian-vpn==4.0.0")
    return _Install.runs


def _cluster() -> ClusterConfig:
    fleet_id = "0f1e2d3c-4b5a-4968-8776-655443322110"
    return ClusterConfig(panel=PanelConfig(url="https://198.51.100.12/panel/"), fleet_id=fleet_id)


def test_the_service_installs_on_the_migration_port_with_the_imports_token(no_install: list[Any]) -> None:
    conn = MockConnection()
    conn.when("ss -H", stdout="")

    install_beside_panel(conn, _cluster(), "service-token")

    (install,) = no_install
    assert install.config.listen_port == MIGRATION_SERVICE_PORT and install.config.fleet_id == _cluster().fleet_id
    assert install.config.admin_token_sha256 == hashlib.sha256(b"service-token").hexdigest()


@pytest.mark.parametrize(
    ("holder", "refused"),
    [("", True), (f'active\n{{"listen_port": {MIGRATION_SERVICE_PORT}}}', False)],
    ids=["something-else", "our-own-service-from-a-previous-run"],
)
def test_a_busy_migration_port_is_refused_unless_our_service_holds_it(
    no_install: list[Any], holder: str, refused: bool
) -> None:
    conn = MockConnection()
    conn.when("ss -H", stdout="LISTEN 0 4096 127.0.0.1:3030 0.0.0.0:*\n")
    conn.when("systemctl is-active ctld", stdout=holder)

    if refused:
        with pytest.raises(MigrationError, match="in use") as error:
            install_beside_panel(conn, _cluster(), "service-token")
        assert error.value.category == "user" and not no_install
    else:
        install_beside_panel(conn, _cluster(), "service-token")
        assert no_install


def _backup_connection(dump: bytes = DUMP, digest_of: bytes = DUMP) -> MockConnection:
    conn = MockConnection()
    conn.when("pg_dump", stdout=f"{hashlib.sha256(digest_of).hexdigest()}\nbackup-done\n")
    conn.when(f"base64 {SERVER_BACKUP_ROOT}/", stdout=base64.encodebytes(dump).decode())
    conn.when("panel.env", stdout="APP_SECRET=secret-for-tests\n")
    return conn


def test_the_backup_keeps_a_private_verified_copy_at_home(tmp_home: Path) -> None:
    _cluster().save()

    local, remote = backup_panel(_backup_connection(), "20261029T120000Z")

    assert remote == f"{SERVER_BACKUP_ROOT}/20261029T120000Z"
    assert local == tmp_home / "backups" / "migrate-20261029T120000Z"
    assert (local / "panel.dump").read_bytes() == DUMP
    assert (local / "panel.env").read_text() == "APP_SECRET=secret-for-tests\n"
    assert (local / "cluster.yml").exists()
    assert oct(local.stat().st_mode & 0o777) == "0o700"
    assert {oct(path.stat().st_mode & 0o777) for path in local.iterdir()} == {"0o600"}


def test_a_dump_that_arrives_damaged_stops_the_migration(tmp_home: Path) -> None:
    with pytest.raises(MigrationError, match="intact"):
        backup_panel(_backup_connection(dump=DUMP[:-1]), "20261029T120000Z")


def test_a_missing_database_container_stops_before_anything_changes(tmp_home: Path) -> None:
    conn = MockConnection()
    conn.when("pg_dump", rc=3, stderr="no panel database container\n")

    with pytest.raises(MigrationError, match="no panel database container"):
        backup_panel(conn, "20261029T120000Z")

    assert not any(call.startswith("base64") for call in conn.calls)


def test_the_dump_reads_either_container_name_and_writes_atomically() -> None:
    conn = MockConnection()
    with pytest.raises(MigrationError):
        backup_panel(conn, "stamp")

    dump = conn.calls[0]
    assert "ctlsvc-db" in dump and "remnawave-db" in dump
    assert "panel.dump.part" in dump and "umask 077" in dump


def test_stopping_the_panel_keeps_it_from_returning_on_reboot() -> None:
    conn = MockConnection()

    stop_panel(conn)

    (command,) = conn.calls
    assert command.index("docker update --restart=no") < command.index("docker stop")
    assert command.index("ctlsvc-web") < command.index("ctlsvc-db")


def test_panel_removal_reuses_teardowns_commands_and_checks_nothing_is_left() -> None:
    conn = MockConnection()
    conn.when("docker ps -a --format", stdout="ctlsvc-db\n/opt/remnawave\n")

    with pytest.raises(MigrationError, match="ctlsvc-db, /opt/remnawave"):
        remove_panel(conn)

    assert conn.calls[: len(panel_stack_removal())] == panel_stack_removal()


def test_panel_removal_passes_once_the_check_finds_nothing() -> None:
    conn = MockConnection()

    remove_panel(conn)

    assert "if [ -e" in conn.calls[-1]


@pytest.mark.parametrize(
    ("pattern", "rc", "message"),
    [
        ("rm -rf", 1, "Could not remove the panel"),
        ("rm -rf", 255, "Could not remove the panel"),
        ("docker ps -a --format", 3, "Could not check"),
        ("docker ps -a --format", 255, "Could not check"),
    ],
    ids=["removal-failed", "removal-lost-connection", "no-docker", "check-lost-connection"],
)
def test_panel_removal_that_fails_or_cannot_be_checked_is_never_done(pattern: str, rc: int, message: str) -> None:
    conn = MockConnection()
    conn.when(pattern, rc=rc, stderr="boom")

    with pytest.raises(MigrationError, match=message):
        remove_panel(conn)


def test_node_removal_deletes_the_container_compose_dir_image_and_api_rule_under_both_names() -> None:
    conn = MockConnection()

    remove_node_runtime(conn)

    command, rule_cleanup = conn.calls
    assert f"docker compose -f {NODE_DIR}/docker-compose.yml down" in command
    assert "netagent" in command and "remnawave-node" in command
    # A failed delete stops the step: the trailing `true` once turned it into a success.
    assert f"rm -rf {NODE_DIR} {LEGACY_NODE_DIR} || exit 1;" in command
    # The control plane's directories are a separate project and stay.
    assert "/opt/remnawave" not in command and "/opt/ctlsvc" not in command
    # The pulled node image goes too; Docker itself (baseline-installed, maybe operator-used) stays.
    assert "docker images -q remnawave/node" in command and "docker image rm $ids" in command
    assert "apt-get" not in command and "purge" not in command
    assert "grep -F -e '# fw-node-api' -e '# Meridian node API (Docker internal)'" in rule_cleanup
    assert "ufw --force delete" in rule_cleanup


def test_node_removal_failure_stops_the_migration_with_dockers_reason() -> None:
    conn = MockConnection()
    conn.when("docker compose", rc=1, stderr="Cannot connect to the Docker daemon")

    with pytest.raises(MigrationError, match="Cannot connect to the Docker daemon"):
        remove_node_runtime(conn)


def test_teardown_removes_the_panel_stack_but_never_the_migration_backup() -> None:
    conn = MockConnection()

    Uninstall().run(conn, ProvisionContext(ip="198.51.100.12"))

    assert all(command in conn.calls for command in panel_stack_removal())
    assert not any(SERVER_BACKUP_ROOT in call or "/var/backups" in call for call in conn.calls)
