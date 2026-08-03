"""Failure-safe contracts for the System Lab stage runner."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_ROOT = Path(__file__).parents[2]
_RUNNER = _ROOT / "tests/systemlab/scripts/controller-run.sh"
_STAGES = (
    "00-bootstrap.sh",
    "10-deploy.sh",
    "20-subscriptions.sh",
    "30-resilience.sh",
)


def _write_stage(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def test_controller_cleans_nested_state_and_preserves_stage_failure(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    stages = scripts / "stages"
    stages.mkdir(parents=True)
    marker = tmp_path / "calls"
    _write_stage(stages / _STAGES[0], 'printf "bootstrap\\n" >> "$CALL_MARKER"; exit 7')
    for stage in _STAGES[1:]:
        _write_stage(stages / stage, f'printf "{stage}\\n" >> "$CALL_MARKER"')
    _write_stage(stages / "90-cleanup.sh", 'printf "cleanup\\n" >> "$CALL_MARKER"')

    result = subprocess.run(
        ["bash", str(_RUNNER)],
        cwd=_ROOT,
        env={
            **os.environ,
            "SYSTEMLAB_SCRIPT_DIR": str(scripts),
            "CALL_MARKER": str(marker),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 7
    assert marker.read_text(encoding="utf-8").splitlines() == ["bootstrap", "cleanup"]
    assert "V4 SYSTEM LAB PASSED" not in result.stdout


def test_controller_reports_success_only_after_cleanup(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    stages = scripts / "stages"
    stages.mkdir(parents=True)
    marker = tmp_path / "calls"
    for stage in _STAGES:
        _write_stage(stages / stage, f'printf "{stage}\\n" >> "$CALL_MARKER"')
    _write_stage(stages / "90-cleanup.sh", 'printf "cleanup\\n" >> "$CALL_MARKER"')

    result = subprocess.run(
        ["bash", str(_RUNNER)],
        cwd=_ROOT,
        env={
            **os.environ,
            "SYSTEMLAB_SCRIPT_DIR": str(scripts),
            "CALL_MARKER": str(marker),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8").splitlines() == [
        _STAGES[0],
        "cleanup",
        *_STAGES[1:],
        "cleanup",
    ]
    assert "V4 SYSTEM LAB PASSED" in result.stdout


def test_controller_fails_before_deploy_when_precleanup_fails(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    stages = scripts / "stages"
    stages.mkdir(parents=True)
    marker = tmp_path / "calls"
    for stage in _STAGES:
        _write_stage(stages / stage, f'printf "{stage}\n" >> "$CALL_MARKER"')
    _write_stage(stages / "90-cleanup.sh", 'printf "cleanup\n" >> "$CALL_MARKER"; exit 9')

    result = subprocess.run(
        ["bash", str(_RUNNER)],
        cwd=_ROOT,
        env={
            **os.environ,
            "SYSTEMLAB_SCRIPT_DIR": str(scripts),
            "CALL_MARKER": str(marker),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 9
    assert marker.read_text(encoding="utf-8").splitlines() == [
        _STAGES[0],
        "cleanup",
        "cleanup",
    ]
    assert "V4 SYSTEM LAB PASSED" not in result.stdout
