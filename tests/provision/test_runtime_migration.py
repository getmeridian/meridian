"""Neutral runtime names: the panel's data and secrets survive the rename byte for byte."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from meridian.config import REMNAWAVE_BACKEND_IMAGE, REMNAWAVE_SUBSCRIPTION_PAGE_IMAGE
from meridian.host_runtime import CONTAINERS, LEGACY_PANEL_DIR, NODE, PANEL_DIR, PANEL_PROJECT, container_role
from meridian.node_deploy import render_node_compose
from meridian.provision import runtime_migration
from meridian.provision.containers import ComposeDeployResult
from meridian.provision.remnawave_panel import DeployRemnawavePanel, render_panel_compose
from meridian.provision.steps import ProvisionContext
from meridian.provision.uninstall import Uninstall
from tests.support.mock_connection import MockConnection
from tests.support.runtime import migrated_panel

_PROXY_WORDS = ("remnawave", "remna", "xray", "vless", "vpn", "proxy", "meridian")


def _panel_compose() -> dict:
    return yaml.safe_load(
        render_panel_compose(
            image=REMNAWAVE_BACKEND_IMAGE,
            panel_port=3000,
            subscription_page_image=REMNAWAVE_SUBSCRIPTION_PAGE_IMAGE,
            subscription_page_host_port=3020,
        )
    )


def test_neutral_names_say_nothing_about_proxy_software() -> None:
    names = [container.name for container in CONTAINERS] + [PANEL_DIR, runtime_migration.NODE.name]
    compose = _panel_compose()
    names += [compose["name"], *compose["services"], *compose["networks"], *compose["volumes"]]
    names += [yaml.safe_load(render_node_compose("remnawave/node:3.4.2"))["name"]]

    assert not [name for name in names if any(word in name.lower() for word in _PROXY_WORDS)]
    assert all(compose["services"][name]["container_name"] == name for name in compose["services"])


def test_postgres_data_is_the_directory_bind_mount_never_a_named_volume() -> None:
    """A named volume would follow the project name; a rename would then start an empty database."""
    compose = _panel_compose()
    database = next(service for service in compose["services"].values() if service["image"].startswith("postgres:"))

    assert compose["name"] == PANEL_PROJECT
    assert database["volumes"] == ["./data:/var/lib/postgresql/data"]
    # The only named volume carries Valkey's socket (persistence is off), never data.
    assert set(compose["volumes"]) == {"valkey-socket"}
    assert "--save" in compose["services"][f"{PANEL_PROJECT}-cache"]["command"]


def test_restart_keeps_database_name_and_role_but_follows_the_service_hostname() -> None:
    conn = migrated_panel()
    conn.when("docker inspect", rc=1)
    conn.when(f"test -f {PANEL_DIR}/.env", rc=0)
    conn.when(f"cat {PANEL_DIR}/.env.subscription", stdout="REMNAWAVE_PANEL_URL=http://remnawave:3000\n")
    conn.when(
        f"cat {PANEL_DIR}/.env",
        stdout="APP_SECRET=oldapp\nMETRICS_PASS=oldmetrics\nPOSTGRES_PASSWORD=olddbpass\n"
        "DATABASE_URL=postgresql://meridian:olddbpass@remnawave-db:5432/remnawave\n",
    )
    captured: dict[str, object] = {}

    def fake_deploy(_conn, work_dir, _compose, *, env_files, dirs, **_kwargs):
        captured.update({env.filename: env.content for env in env_files}, work_dir=work_dir, dirs=dirs)
        return ComposeDeployResult(changed=True)

    with patch("meridian.provision.containers.deploy_compose_stack", side_effect=fake_deploy):
        assert DeployRemnawavePanel().run(conn, ProvisionContext(ip="198.51.100.1")).status == "changed"

    env = str(captured[".env"])
    assert f"DATABASE_URL=postgresql://meridian:olddbpass@{PANEL_PROJECT}-db:5432/remnawave\n" in env
    assert "POSTGRES_USER=meridian\n" in env and "POSTGRES_DB=remnawave\n" in env
    assert f"REMNAWAVE_PANEL_URL=http://{PANEL_PROJECT}-api:3000\n" in str(captured[".env.subscription"])
    assert captured["work_dir"] == PANEL_DIR and captured["dirs"] == [f"{PANEL_DIR}/data"]


def _deploy(conn: MockConnection) -> tuple[str, str]:
    with patch("meridian.provision.containers.deploy_compose_stack", return_value=ComposeDeployResult(changed=True)):
        result = DeployRemnawavePanel().run(conn, ProvisionContext(ip="198.51.100.1"))
    return result.status, result.detail


def test_panel_migration_stops_the_legacy_stack_then_moves_the_directory_before_reading_it() -> None:
    conn = migrated_panel("legacy-dir", "container:remnawave", "container:remnawave-db")
    conn.when("docker stop -t 60", rc=0)
    conn.when("docker inspect", rc=1)
    conn.when(f"cat {PANEL_DIR}/.env", stdout="APP_SECRET=a\nMETRICS_PASS=m\nPOSTGRES_PASSWORD=p\n")

    assert _deploy(conn)[0] == "changed"

    calls = conn.calls
    stop = next(i for i, call in enumerate(calls) if "docker stop -t 60" in call)
    move = next(i for i, call in enumerate(calls) if f"mv {LEGACY_PANEL_DIR} {PANEL_DIR}" in call)
    first_read = next(i for i, call in enumerate(calls) if f"cat {PANEL_DIR}/" in call)
    assert stop < move < first_read
    # Dependents stop first; the stack is removed, its data directory is never touched.
    assert calls[stop].index("remnawave-subscription-page") < calls[stop].index("remnawave-db")
    assert not any("rm -rf" in call or "down -v" in call or "--volumes" in call for call in calls)


@pytest.mark.parametrize(
    ("facts", "reason"),
    [
        (("legacy-dir", "current-dir", "container:remnawave"), "both"),
        (("legacy-dir", f"mount:{LEGACY_PANEL_DIR}/data", "container:remnawave"), "mount point"),
    ],
    ids=["both-directories", "mounted"],
)
def test_ambiguous_panel_migration_refuses_before_stopping_anything(facts: tuple[str, ...], reason: str) -> None:
    conn = migrated_panel(*facts)

    status, detail = _deploy(conn)

    assert status == "failed" and reason in detail
    assert not any("docker stop" in call or "mv " in call for call in conn.calls)


def test_unavailable_docker_refuses_the_panel_migration() -> None:
    conn = MockConnection()
    conn.when(runtime_migration._PROBE_DONE, rc=3, stdout="legacy-dir\n", stderr="Cannot connect to the Docker daemon")

    status, detail = _deploy(conn)

    assert status == "failed" and "Cannot connect to the Docker daemon" in detail
    assert not any("mv " in call for call in conn.calls)


_FAKE_DOCKER = """#!/bin/sh
echo "$*" >> "$DOCKER_LOG"
case "$1" in
  ps) cat "$DOCKER_STATE" ;;
  inspect) for last; do :; done; grep -qx "$last" "$DOCKER_STATE" ;;
  rm) grep -vx "$2" "$DOCKER_STATE" > "$DOCKER_STATE.new"; mv "$DOCKER_STATE.new" "$DOCKER_STATE" ;;
esac
"""


class _Shell:
    """Runs commands in a real shell against a fake docker, as ServerConnection would on the server."""

    def __init__(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "docker").write_text(_FAKE_DOCKER)
        (bin_dir / "docker").chmod(0o755)
        self.state = tmp_path / "containers"
        self.log = tmp_path / "docker.log"
        self.log.touch()
        self.env = {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "DOCKER_STATE": str(self.state),
            "DOCKER_LOG": str(self.log),
        }

    def run(self, command: str, timeout: int = 30, **_kwargs: object) -> SimpleNamespace:
        done = subprocess.run(["/bin/sh", "-c", command], env=self.env, capture_output=True, text=True, timeout=timeout)
        return SimpleNamespace(returncode=done.returncode, stdout=done.stdout, stderr=done.stderr)


def test_interrupted_panel_migration_resumes_and_keeps_every_byte(tmp_path: Path, monkeypatch) -> None:
    legacy, current = tmp_path / "opt" / "remnawave", tmp_path / "opt" / PANEL_PROJECT
    monkeypatch.setattr(runtime_migration, "LEGACY_PANEL_DIR", str(legacy))
    monkeypatch.setattr(runtime_migration, "PANEL_DIR", str(current))
    files = {".env": b"POSTGRES_PASSWORD=olddbpass\nAPP_SECRET=oldapp\n", "data/PG_VERSION": b"17\n"}
    for name, content in files.items():
        (legacy / name).parent.mkdir(parents=True, exist_ok=True)
        (legacy / name).write_bytes(content)
    shell = _Shell(tmp_path)
    # Interrupted after the old containers stopped: the directory is still in place.
    shell.state.write_text("unrelated-app\n")

    assert runtime_migration.adopt_legacy_panel(shell) == ""  # type: ignore[arg-type]

    assert not legacy.exists()
    assert {name: (current / name).read_bytes() for name in files} == files
    assert runtime_migration.adopt_legacy_panel(shell) == ""  # type: ignore[arg-type]
    assert "stop" not in shell.log.read_text() and "unrelated-app" in shell.state.read_text()


def test_live_legacy_panel_is_stopped_and_removed_before_its_directory_moves(tmp_path: Path, monkeypatch) -> None:
    legacy, current = tmp_path / "remnawave", tmp_path / PANEL_PROJECT
    monkeypatch.setattr(runtime_migration, "LEGACY_PANEL_DIR", str(legacy))
    monkeypatch.setattr(runtime_migration, "PANEL_DIR", str(current))
    (legacy / "data").mkdir(parents=True)
    shell = _Shell(tmp_path)
    shell.state.write_text("remnawave\nremnawave-db\nremnawave-redis\nremnawave-subscription-page\nother\n")

    assert runtime_migration.adopt_legacy_panel(shell) == ""  # type: ignore[arg-type]

    assert (current / "data").is_dir() and not legacy.exists()
    assert shell.state.read_text() == "other\n"
    stops = [line.split()[-1] for line in shell.log.read_text().splitlines() if line.startswith("stop")]
    assert stops == ["remnawave-subscription-page", "remnawave", "remnawave-redis", "remnawave-db"]


def test_teardown_removes_runtimes_under_both_names() -> None:
    conn = MockConnection()
    Uninstall().run(conn, ProvisionContext(ip="198.51.100.1"))
    script = "\n".join(conn.calls)

    for directory in (PANEL_DIR, LEGACY_PANEL_DIR, "/opt/netagent", "/opt/remnanode"):
        assert f"rm -rf {directory}" in script
    for container in CONTAINERS:
        assert container.name in script and container.legacy in script


def test_container_roles_cover_current_and_pre_rename_names() -> None:
    assert container_role(NODE.name) == "Exit node"
    assert container_role("remnawave-db").startswith("Panel database, pre-rename")
    assert container_role("unrelated-app") == ""
