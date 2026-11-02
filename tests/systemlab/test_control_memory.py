"""Control-service memory evidence must survive the single-server workload."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections import Counter
from pathlib import Path

import httpx
import pytest
import yaml

from meridian.control.client import ControlClient
from meridian.core.delivery_geodata import GeoData
from meridian.provision.control_service import render_service_unit
from tests.support.control_service import ADMIN_TOKEN
from tests.support.token_service import serve_tokens
from tests.test_control_subscription_corpus import FLEETS
from tests.test_delivery_geodata import dataset

_LAB = Path(__file__).parent
_READER = _LAB / "scripts/read-control-memory.sh"
_INVOCATION = "0123456789abcdef0123456789abcdef"


def read_memory(
    tmp_path: Path,
    *,
    peak: str = "114294784",
    fallback: str | None = None,
    invocation: str = _INVOCATION,
    active: str = "active",
    accounting: str = "yes",
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    systemctl = bin_dir / "systemctl"
    systemctl.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "properties = json.loads(os.environ['SYSTEMLAB_MEMORY_PROPERTIES'])\n"
        "print(properties.get(sys.argv[4], ''))\n",
        encoding="utf-8",
    )
    systemctl.chmod(0o755)
    cgroups = tmp_path / "cgroups"
    unit = cgroups / "system.slice/ctld.service"
    unit.mkdir(parents=True)
    if fallback is not None:
        (unit / "memory.peak").write_text(fallback + "\n", encoding="utf-8")
    properties = {
        "ActiveState": active,
        "InvocationID": invocation,
        "MemoryAccounting": accounting,
        "MemoryPeak": peak,
        "MemoryMax": "268435456",
        "ControlGroup": "/system.slice/ctld.service",
    }
    return subprocess.run(
        ["bash", str(_READER), _INVOCATION],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "SYSTEMLAB_CGROUP_ROOT": str(cgroups),
            "SYSTEMLAB_MEMORY_PROPERTIES": json.dumps(properties),
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )


def test_single_deploy_checks_memory_before_redeploy_or_teardown() -> None:
    script = (_LAB / "scripts/stages/05-single-deploy.sh").read_text(encoding="utf-8")
    memory = 'bash "$SYSTEMLAB_ROOT/scripts/stages/06-control-memory.sh"'
    assert memory in script, "the single-server deploy never exercises or measures control-service memory"
    assert script.index('wait_for_agent "$EXIT_B_IP"') < script.index(memory)
    assert script.index(memory) < script.index('meridian deploy "$EXIT_B_IP" --client-name solo --yes\n')
    assert script.index(memory) < script.index('meridian teardown "$EXIT_B_IP"')


def test_single_server_has_delegated_memory_accounting() -> None:
    compose = yaml.safe_load((_LAB / "compose.yml").read_text(encoding="utf-8"))
    server = compose["services"]["exit-b"]
    assert server["privileged"] is True  # Docker gives systemd a writable cgroup mount.
    assert server["cgroup"] == "private"  # systemd must manage only this container's subtree.
    dockerfile = (_LAB / "images/base/Dockerfile").read_text(encoding="utf-8")
    assert "DefaultMemoryAccounting=yes" in dockerfile


def test_memory_budget_keeps_reviewed_headroom_below_the_rendered_limit() -> None:
    from tests.systemlab.control_memory import CONTROL_MEMORY_BUDGET_BYTES

    limit = re.search(r"^MemoryMax=(\d+)M$", render_service_unit(), re.MULTILINE)
    assert limit is not None
    maximum = int(limit[1]) * 1024 * 1024
    assert CONTROL_MEMORY_BUDGET_BYTES * 8 == maximum * 5, "review MemoryMax and the lab budget together"


def test_memory_burst_fits_the_control_listener_accept_queue() -> None:
    from meridian.control.server import ControlServer
    from tests.systemlab.control_memory import BURST_CONCURRENCY

    assert 1 < BURST_CONCURRENCY < ControlServer.request_queue_size


@pytest.mark.parametrize("available", [True, False], ids=["cached-geodata", "missing-geodata"])
def test_memory_workload_exercises_live_read_paths_and_refuses_missing_geodata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, available: bool
) -> None:
    from meridian.control import service
    from tests.systemlab.control_memory import BURST_CONCURRENCY, exercise_memory_paths

    data = GeoData.from_bytes(*dataset()) if available else None
    monkeypatch.setattr(service, "cached_geodata", lambda _directory: data)
    paths: list[str] = []
    with serve_tokens(tmp_path) as fleet:
        user = fleet.service.put_user("solo")
        for index, endpoint in enumerate(FLEETS["single"].endpoints):
            fleet.service.put_endpoint(f"entry-{index}", endpoint)
        changes = fleet.service._store.db.total_changes
        with (
            ControlClient(fleet.url, ADMIN_TOKEN, report_mutations=False) as client,
            httpx.Client(
                timeout=20,
                limits=httpx.Limits(max_connections=BURST_CONCURRENCY),
                event_hooks={"request": [lambda request: paths.append(request.url.path)]},
            ) as public,
        ):
            if available:
                exercise_memory_paths(client, "solo", public, f"{fleet.url}/sub/{user.sub_token}")
            else:
                with pytest.raises(ValueError, match="geodata is unavailable"):
                    exercise_memory_paths(client, "solo", public, f"{fleet.url}/sub/{user.sub_token}")
        assert fleet.service._store.db.total_changes == changes
    expected = {
        f"/sub/{user.sub_token}" + (f"/{suffix}" if suffix else ""): 8 for suffix in ("", "json", "mihomo", "singbox")
    }
    assert Counter(paths) == (expected if available else {})


def test_reader_uses_the_unit_peak_in_bytes(tmp_path: Path) -> None:
    result = read_memory(tmp_path, fallback="1")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "114294784 268435456"


@pytest.mark.parametrize("peak", ["", "[not set]", "0", "18446744073709551615"])
def test_reader_falls_back_to_the_same_unit_cgroup(tmp_path: Path, peak: str) -> None:
    result = read_memory(tmp_path, peak=peak, fallback="125829120")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "125829120 268435456"


@pytest.mark.parametrize("fallback", [None, "0", "[not set]", "18446744073709551615"])
def test_unavailable_memory_evidence_fails_loudly(tmp_path: Path, fallback: str | None) -> None:
    result = read_memory(tmp_path, peak="[not set]", fallback=fallback)
    assert result.returncode == 1
    assert "memory peak is unavailable" in result.stderr
    assert not result.stdout.strip()


def test_disabled_memory_accounting_cannot_pass(tmp_path: Path) -> None:
    result = read_memory(tmp_path, accounting="no")
    assert result.returncode == 1
    assert "memory accounting is disabled" in result.stderr


@pytest.mark.parametrize(
    ("invocation", "active", "message"),
    [("different", "active", "restarted"), (_INVOCATION, "failed", "not active")],
)
def test_a_crashed_or_restarted_service_cannot_supply_a_fresh_lower_peak(
    tmp_path: Path, invocation: str, active: str, message: str
) -> None:
    result = read_memory(tmp_path, invocation=invocation, active=active)
    assert result.returncode == 1
    assert message in result.stderr


@pytest.mark.parametrize("extra", [0, 1])
def test_peak_above_the_budget_fails_and_always_prints_the_measurement(
    extra: int, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.systemlab.control_memory import CONTROL_MEMORY_BUDGET_BYTES, check_memory_peak

    peak = CONTROL_MEMORY_BUDGET_BYTES + extra
    if extra:
        with pytest.raises(ValueError, match="exceeds"):
            check_memory_peak(peak, 256 * 1024 * 1024)
    else:
        check_memory_peak(peak, 256 * 1024 * 1024)
    output = capsys.readouterr().out
    assert str(peak) in output and "160.0 MiB" in output and "256.0 MiB" in output


def test_memory_stage_waits_for_the_background_geodata_mirror_and_then_fails_loudly() -> None:
    from types import SimpleNamespace

    from tests.systemlab.control_memory import wait_for_geodata

    answers = iter([False, False, True])
    calls: list[float] = []

    class Client:
        def explain_delivery_policy(self, _request: object) -> SimpleNamespace:
            return SimpleNamespace(geodata_available=next(answers))

    now = [0.0]
    wait_for_geodata(
        Client(),
        timeout=10,
        sleep=lambda pause: (calls.append(pause), now.__setitem__(0, now[0] + pause)) and None,
        clock=lambda: now[0],
    )  # type: ignore[arg-type]
    assert calls == [2.0, 2.0]

    answers = iter([False] * 10)
    now = [0.0]
    with pytest.raises(ValueError, match="unavailable to the control service after 5s"):
        wait_for_geodata(
            Client(), timeout=5, sleep=lambda pause: now.__setitem__(0, now[0] + pause), clock=lambda: now[0]
        )  # type: ignore[arg-type]
