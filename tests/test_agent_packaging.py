"""The node agent builds ride in the wheel: the build hook, the release check, and how Meridian finds them."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pytest

from meridian import control_artifacts
from meridian.control_artifacts import AGENT_TARGETS, agent_binary
from meridian.core.errors import LocalStateError
from scripts import hatch_build, verify_package_assets
from scripts.hatch_build import CustomBuildHook
from scripts.verify_package_assets import verify_wheel_assets

BUILD = b"\x7fELF fake static netagent"


def _write_build(directory: Path, target: str, data: bytes = BUILD, *, digest: str | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"netagent-{target}"
    path.write_bytes(data)
    (directory / f"netagent-{target}.sha256").write_text((digest or hashlib.sha256(data).hexdigest()) + "\n")
    (directory / f"netagent-{target}.version").write_text("0.2.1+abc\n")
    return path


def _hook(root: Path, target_name: str) -> CustomBuildHook:
    return CustomBuildHook(str(root), {}, None, None, str(root / "build"), target_name)  # type: ignore[arg-type]


def test_every_agent_target_is_built_packaged_and_checked() -> None:
    builds = {f"netagent-{target}" for target in AGENT_TARGETS.values()}

    assert set(hatch_build.AGENT_BUILDS) == builds == set(verify_package_assets.AGENT_BUILDS)


@pytest.mark.parametrize(
    ("target_name", "destination"),
    [("wheel", "meridian/agent_dist"), ("sdist", "src/meridian/agent_dist")],
)
def test_the_build_hook_packages_each_agent_build_with_its_sidecars(
    tmp_path: Path, target_name: str, destination: str
) -> None:
    for build in hatch_build.AGENT_BUILDS:
        _write_build(tmp_path / "agent" / "dist", build.removeprefix("netagent-"))
    build_data: dict[str, Any] = {"force_include": {}}

    _hook(tmp_path, target_name).initialize("standard", build_data)

    packaged = sorted(build_data["force_include"].values())
    assert len(packaged) == 6
    assert f"{destination}/netagent-aarch64-unknown-linux-musl.sha256" in packaged
    assert all(value.startswith(destination + "/") for value in packaged)


def test_a_source_build_without_agent_builds_still_works(tmp_path: Path) -> None:
    build_data: dict[str, Any] = {"force_include": {}}

    _hook(tmp_path, "wheel").initialize("standard", build_data)

    assert build_data["force_include"] == {}


def _wheel(tmp_path: Path, builds: dict[str, bytes], *, digest: str | None = None) -> Path:
    wheel = tmp_path / "meridian.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr("meridian/studio_assets/studio/index.html", "<!doctype html>")
        archive.writestr("meridian/data/agents.md", "# Meridian for AI agents")
        archive.writestr("meridian/data/ai-reference.md", "# Reference")
        for name, data in builds.items():
            path = verify_package_assets.AGENT_PREFIX + name
            archive.writestr(path, data)
            archive.writestr(path + ".sha256", (digest or hashlib.sha256(data).hexdigest()) + "\n")
            archive.writestr(path + ".version", "0.2.1+abc\n")
    return wheel


def test_a_release_wheel_needs_both_agent_builds(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, {"netagent-x86_64-unknown-linux-musl": BUILD})

    verify_wheel_assets(wheel)  # CI's website wheel carries no agent
    with pytest.raises(ValueError, match="missing the node agent: .*aarch64"):
        verify_wheel_assets(wheel, release=True)
    with pytest.raises(ValueError, match="missing the node agent"):
        verify_wheel_assets(wheel, studio=False, agent=True)


def test_a_packaged_agent_build_that_misses_its_digest_fails_the_check(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, dict.fromkeys(verify_package_assets.AGENT_BUILDS, BUILD), digest="0" * 64)

    with pytest.raises(ValueError, match="does not match its sha256 file"):
        verify_wheel_assets(wheel, release=True)


def test_a_complete_release_wheel_passes(tmp_path: Path) -> None:
    verify_wheel_assets(_wheel(tmp_path, dict.fromkeys(verify_package_assets.AGENT_BUILDS, BUILD)), release=True)


@pytest.fixture
def packaged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "site-packages" / "meridian" / "agent_dist"
    monkeypatch.setattr(control_artifacts, "PACKAGED_AGENT_DIR", directory)
    monkeypatch.delenv("MERIDIAN_AGENT_BINARY", raising=False)
    checkout = tmp_path / "checkout"
    monkeypatch.setattr(control_artifacts, "source_checkout", lambda: checkout)
    return directory


def test_the_packaged_build_wins_over_a_checkout_and_the_override_over_both(
    packaged: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_build(tmp_path / "checkout" / "agent" / "dist", "x86_64-unknown-linux-musl", b"checkout build")
    assert agent_binary("x86_64").data == b"checkout build"

    _write_build(packaged, "x86_64-unknown-linux-musl", b"packaged build")
    assert agent_binary("x86_64").data == b"packaged build"

    override = tmp_path / "netagent"
    override.write_bytes(b"override build")  # a hand-built binary needs no sidecar
    monkeypatch.setenv("MERIDIAN_AGENT_BINARY", str(override))
    assert agent_binary("x86_64").data == b"override build"


@pytest.mark.parametrize("digest", ["0" * 64, None], ids=["mismatch", "missing"])
def test_a_packaged_build_must_match_its_sha256_file(packaged: Path, digest: str | None) -> None:
    path = _write_build(packaged, "x86_64-unknown-linux-musl", digest=digest or "0" * 64)
    if digest is None:
        path.with_name(path.name + ".sha256").unlink()

    with pytest.raises(LocalStateError, match="does not match its sha256 file"):
        agent_binary("x86_64")


def test_a_checkout_build_with_a_wrong_sha256_file_is_refused(packaged: Path, tmp_path: Path) -> None:
    _write_build(tmp_path / "checkout" / "agent" / "dist", "x86_64-unknown-linux-musl", digest="0" * 64)

    with pytest.raises(LocalStateError, match="does not match"):
        agent_binary("x86_64")


def test_an_architecture_with_no_build_names_the_supported_ones(packaged: Path) -> None:
    with pytest.raises(LocalStateError, match="no build for riscv64") as error:
        agent_binary("riscv64")

    assert error.value.hint == "Use a server with one of: aarch64, x86_64."


def test_an_arm_server_gets_the_arm_build(packaged: Path) -> None:
    _write_build(packaged, "aarch64-unknown-linux-musl", b"arm build")

    assert agent_binary("aarch64").data == b"arm build"


def test_a_node_is_compared_with_its_own_architectures_build(packaged: Path) -> None:
    """A checkout that rebuilt one target ships two IDs; an ARM node on the ARM build was called outdated."""
    from meridian.control_artifacts import expected_agent_version

    _write_build(packaged, "x86_64-unknown-linux-musl")
    arm = _write_build(packaged, "aarch64-unknown-linux-musl", b"arm build")
    arm.with_name(arm.name + ".version").write_text("0.2.1+def\n")

    assert expected_agent_version("0.2.1+def") == "0.2.1+def"
    assert expected_agent_version("0.2.1+abc") == "0.2.1+abc"
    assert expected_agent_version("0.2.0+old") in {"0.2.1+abc", "0.2.1+def"}
    assert expected_agent_version(None) in {"0.2.1+abc", "0.2.1+def"}
