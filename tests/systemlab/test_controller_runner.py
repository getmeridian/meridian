"""Failure-safe contracts for the System Lab stage runner."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_ROOT = Path(__file__).parents[2]
_RUNNER = _ROOT / "tests/systemlab/scripts/controller-run.sh"
_STAGES = (
    "00-bootstrap.sh",
    "05-single-deploy.sh",
    "07-panel-domain.sh",
    "10-deploy.sh",
    "20-subscriptions.sh",
    "25-golden.sh",
    "30-resilience.sh",
    "40-removal.sh",
)


def _write_stage(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\nset -euo pipefail\n{body}\n", encoding="utf-8")
    path.chmod(0o755)


def _run(scripts: Path, marker: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_RUNNER)],
        cwd=_ROOT,
        env={**os.environ, "SYSTEMLAB_SCRIPT_DIR": str(scripts), "CALL_MARKER": str(marker), "SYSTEMLAB_KEEP": ""},
        text=True,
        capture_output=True,
        check=False,
    )


def test_controller_stops_at_the_first_failing_stage_and_keeps_its_status(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    stages = scripts / "stages"
    stages.mkdir(parents=True)
    marker = tmp_path / "calls"
    _write_stage(stages / _STAGES[0], 'printf "bootstrap\\n" >> "$CALL_MARKER"')
    _write_stage(stages / _STAGES[1], 'printf "single\\n" >> "$CALL_MARKER"; exit 7')
    for stage in _STAGES[2:]:
        _write_stage(stages / stage, f'printf "{stage}\\n" >> "$CALL_MARKER"')

    result = _run(scripts, marker)

    assert result.returncode == 7
    assert marker.read_text(encoding="utf-8").splitlines() == ["bootstrap", "single"]
    assert "V4 SYSTEM LAB PASSED" not in result.stdout


def test_controller_reports_success_only_after_every_stage(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    stages = scripts / "stages"
    stages.mkdir(parents=True)
    marker = tmp_path / "calls"
    for stage in _STAGES:
        _write_stage(stages / stage, f'printf "{stage}\\n" >> "$CALL_MARKER"')

    result = _run(scripts, marker)

    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8").splitlines() == list(_STAGES)
    assert "V4 SYSTEM LAB PASSED" in result.stdout
