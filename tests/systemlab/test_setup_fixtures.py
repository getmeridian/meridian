"""Executable contracts for generated System Lab fixtures."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[2]
_SCRIPT = _ROOT / "tests/systemlab/scripts/setup-fixtures.sh"


def _run_setup(
    tmp_path: Path,
    *,
    current_ca: str | None,
    extracted_ca: str = "pinned pebble ca\n",
    fail_copy: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(fixtures / "id_ed25519")],
        check=True,
    )
    (fixtures / "id_ed25519.pub").write_text("stale public key\n", encoding="utf-8")
    (fixtures / "controller_authorized_keys").write_text("stale authorized key\n", encoding="utf-8")
    if current_ca is not None:
        (fixtures / "pebble-ca.pem").write_text(current_ca, encoding="utf-8")

    extracted = tmp_path / "pinned-ca.pem"
    extracted.write_text(extracted_ca, encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
case "${1-}" in
  create)
    printf 'fixture-container\n'
    ;;
  cp)
    if [ "${FAKE_DOCKER_FAIL_COPY-}" = "1" ]; then
      exit 1
    fi
    cp "$FAKE_PEBBLE_CA" "$3"
    ;;
  rm)
    ;;
  *)
    exit 2
    ;;
esac
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    result = subprocess.run(
        ["bash", str(_SCRIPT)],
        cwd=_ROOT,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "SYSTEMLAB_FIXTURES_DIR": str(fixtures),
            "FAKE_PEBBLE_CA": str(extracted),
            "FAKE_DOCKER_FAIL_COPY": "1" if fail_copy else "0",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    return result, fixtures / "pebble-ca.pem"


@pytest.mark.parametrize("current_ca", [None, "stale ca\n"])
def test_setup_fixtures_installs_ca_from_pinned_pebble_image(tmp_path: Path, current_ca: str | None) -> None:
    result, ca_path = _run_setup(tmp_path, current_ca=current_ca)

    assert result.returncode == 0, result.stderr
    assert ca_path.read_text(encoding="utf-8") == "pinned pebble ca\n"
    public_key = (ca_path.parent / "id_ed25519.pub").read_text(encoding="utf-8")
    assert public_key.startswith("ssh-ed25519 ")
    assert (ca_path.parent / "controller_authorized_keys").read_text(encoding="utf-8") == public_key


def test_setup_fixtures_fails_closed_without_replacing_ca_on_extraction_error(tmp_path: Path) -> None:
    result, ca_path = _run_setup(tmp_path, current_ca="existing ca\n", fail_copy=True)

    assert result.returncode != 0
    assert "Could not extract the root CA" in result.stderr
    assert ca_path.read_text(encoding="utf-8") == "existing ca\n"
