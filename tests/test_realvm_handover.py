"""Negative evidence for the local-only b12 recovery drill; never access a cloud provider."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from meridian.control_artifacts import ServicePackage
from meridian.ssh import CommandResult
from tests.realvm import handover_drill as drill
from tests.realvm import orchestrator, upgrade_verifier
from tests.realvm.upgrade_support import VerificationError, build_intent


def test_b12_deploy_fixture_carries_a_non_latin_declared_person() -> None:
    intent = json.loads(build_intent("srv-aaaaaaaaaaaa", "198.51.100.10", legacy=True, display_name=drill.DISPLAY))
    assert intent["access"]["users"] == ["default"]
    assert intent["access"]["display_names"] == {"default": drill.DISPLAY}
    assert "users" not in json.loads(build_intent("srv-aaaaaaaaaaaa", "198.51.100.10"))["access"]


def test_unstartable_published_cli_refuses_before_a_provider_can_be_created(monkeypatch) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(orchestrator, "require_agent_build", lambda: None)
    monkeypatch.setattr(upgrade_verifier, "preflight_upgrade", lambda _root, _env: (drill.SOURCE, "0.2"))
    monkeypatch.setattr(orchestrator, "make_provider", lambda *_: pytest.fail("Provisioning started"))

    def fail_install(self, version):
        assert version == drill.SOURCE and self.old_env["UV_EXCLUDE_NEWER"] == drill.AS_OF
        raise VerificationError("b12 cannot start", exit_code=2)

    monkeypatch.setattr(upgrade_verifier.UpgradeVerifier, "install_previous", fail_install)
    assert orchestrator.cmd_up(SimpleNamespace(topology="upgrade", keep=False)) == 2


@pytest.fixture
def run(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return upgrade_verifier.UpgradeVerifier(tmp_path, tmp_path / "work", "198.51.100.10", {"MERIDIAN_HOME": str(home)})


def test_same_version_pypi_install_cannot_pass_as_a_checkout(run, monkeypatch) -> None:
    artifact = ServicePackage(requirement="", wheel=b"local wheel", wheel_name="meridian_vpn-4.0-py3-none-any.whl")
    monkeypatch.setattr(drill, "service_package", lambda: artifact)
    execute = Mock(return_value=CommandResult(args=[], returncode=0, stdout="meridian-vpn==4.0"))
    monkeypatch.setattr(run, "execute", execute)
    with pytest.raises(VerificationError, match="wheel built from this checkout"):
        drill.HandoverDrill(run).verify_checkout_install()
    execute.return_value = CommandResult(args=[], returncode=0, stdout=artifact.identity)
    drill.HandoverDrill(run).verify_checkout_install()


def test_interrupt_must_leave_the_pending_checkpoint_and_client_list_must_finish_it(run, monkeypatch) -> None:
    monkeypatch.setattr(run, "execute", Mock())
    monkeypatch.setattr(run, "people", lambda: ["bob", "default"])
    pending = SimpleNamespace(people_adoption=SimpleNamespace(state="pending", users=["default", "bob"]))
    with patch.object(drill.ClusterConfig, "load", side_effect=[pending, pending]):
        with pytest.raises(VerificationError, match="client list did not finish"):
            drill.HandoverDrill(run).apply_and_resume(["bob", "default"])


def test_stale_copy_refusal_for_an_unrelated_error_is_not_evidence(run, monkeypatch) -> None:
    home = run.env["MERIDIAN_HOME"]

    def unrelated(*args, **kwargs):
        assert run.env["MERIDIAN_HOME"] != home
        return CommandResult(args=[], returncode=2, stderr="invalid cluster file")

    monkeypatch.setattr(run, "execute", unrelated)
    with pytest.raises(VerificationError, match="expected fleet-fence"):
        drill.HandoverDrill(run).check_stale_copy()
    assert run.env["MERIDIAN_HOME"] == home


def test_live_database_cannot_pass_as_a_successful_restore(run, monkeypatch) -> None:
    home = Path(run.env["MERIDIAN_HOME"])
    (home / "device.key").write_bytes(b"test key")
    (home / "cluster.yml").write_text("source should not enter restore home")
    marker = ["after-snapshot", "default"]
    monkeypatch.setattr(run, "people", lambda: marker)

    def execute(label, args, **kwargs):
        if "Stop the lab" in label:
            assert sorted(p.name for p in home.iterdir()) == ["device.key"]
        if "Restore the pre-handover" in label:
            assert "--force" not in args
        return CommandResult(args=args, returncode=0)

    monkeypatch.setattr(run, "execute", execute)
    with patch.object(
        drill.ClusterConfig,
        "load",
        return_value=SimpleNamespace(people_adoption=None, control_restore=SimpleNamespace(step="done", fleet_epoch=2)),
    ):
        with pytest.raises(VerificationError, match="original people"):
            drill.HandoverDrill(run).restore_snapshot(["default"])


def test_b12_recovery_will_not_replace_an_unmarked_or_wrong_fleet_home(run) -> None:
    drill_run = drill.HandoverDrill(run)
    run.env["MERIDIAN_FLEET_ID"] = "this-lab"
    marker = Path(run.env["MERIDIAN_HOME"]).parent / "fleet-id"
    for content in (None, "other-lab"):
        if content is not None:
            marker.write_text(content)
        with pytest.raises(VerificationError, match="disposable orchestrator workspace"):
            drill_run.require_lab_workspace()
    marker.write_text("this-lab\n")
    drill_run.require_lab_workspace()
