"""Tests for auto-update logic."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from meridian.update import _should_check, check_for_update, do_upgrade, run_self_update, verify_installed_version


class TestShouldCheck:
    def test_first_check_returns_true(self, tmp_home: Path) -> None:
        with patch("meridian.update.CACHE_DIR", tmp_home / "cache"):
            assert _should_check() is True

    def test_second_check_within_interval_returns_false(self, tmp_home: Path) -> None:
        with patch("meridian.update.CACHE_DIR", tmp_home / "cache"):
            assert _should_check() is True
            assert _should_check() is False

    def test_check_after_interval_returns_true(self, tmp_home: Path) -> None:
        with patch("meridian.update.CACHE_DIR", tmp_home / "cache"), patch("meridian.update.UPDATE_CHECK_INTERVAL", 0):
            assert _should_check() is True
            assert _should_check() is True  # interval is 0, always check

    def test_unwritable_cache_disables_optional_check(self) -> None:
        with patch("meridian.update.CACHE_DIR", Path("/dev/null/cache")):
            assert _should_check() is False


class TestVersionComparison:
    def test_patch_bump_detected(self) -> None:
        from packaging.version import Version

        current = Version("1.2.5")
        remote = Version("1.2.6")
        assert remote > current
        assert current.major == remote.major
        assert current.minor == remote.minor

    def test_minor_bump_detected(self) -> None:
        from packaging.version import Version

        current = Version("1.2.5")
        remote = Version("1.3.0")
        assert remote > current
        assert current.major == remote.major
        assert current.minor != remote.minor

    def test_major_bump_detected(self) -> None:
        from packaging.version import Version

        current = Version("1.2.5")
        remote = Version("2.0.0")
        assert remote > current
        assert current.major != remote.major

    def test_no_downgrade(self) -> None:
        from packaging.version import Version

        current = Version("2.0.0")
        remote = Version("1.2.5")
        assert remote <= current


class TestCheckForUpdate:
    def test_patch_bump_does_not_auto_upgrade(self) -> None:
        with (
            patch("meridian.update._should_check", return_value=True),
            patch("meridian.update.get_pypi_latest", return_value="1.2.6"),
            patch("meridian.update.do_upgrade") as mock_upgrade,
            patch("meridian.update.err_console.print") as mock_print,
        ):
            check_for_update("1.2.5")

        mock_upgrade.assert_not_called()
        rendered = " ".join(str(call.args[0]) for call in mock_print.call_args_list if call.args)
        assert "meridian update" in rendered

    def test_same_version_no_output(self) -> None:
        with (
            patch("meridian.update._should_check", return_value=True),
            patch("meridian.update.get_pypi_latest", return_value="1.2.5"),
            patch("meridian.update.err_console.print") as mock_print,
        ):
            check_for_update("1.2.5")

        mock_print.assert_not_called()

    def test_disable_update_check_env_flag(self, monkeypatch) -> None:
        import importlib

        import meridian.config as config

        monkeypatch.setenv("MERIDIAN_DISABLE_UPDATE_CHECK", "1")
        importlib.reload(config)
        assert config.DISABLE_UPDATE_CHECK is True


class TestRunSelfUpdate:
    def test_unavailable_pypi_returns_system_exit_code(self) -> None:
        with patch("meridian.update.get_pypi_latest", return_value=None):
            assert run_self_update() == 3

    def test_invalid_remote_version_returns_system_exit_code(self) -> None:
        with patch("meridian.update.get_pypi_latest", return_value="not-a-version"):
            assert run_self_update() == 3

    def test_current_version_returns_success(self) -> None:
        with (
            patch("meridian.__version__", "4.0.0"),
            patch("meridian.update.get_pypi_latest", return_value="4.0.0"),
        ):
            assert run_self_update() == 0

    def test_upgrade_failure_returns_system_exit_code(self) -> None:
        with (
            patch("meridian.__version__", "4.0.0"),
            patch("meridian.update.get_pypi_latest", return_value="4.0.1"),
            patch("meridian.update.do_upgrade", return_value=False),
        ):
            assert run_self_update() == 3

    def test_successful_upgrade_returns_success(self) -> None:
        with (
            patch("meridian.__version__", "4.0.0"),
            patch("meridian.update.get_pypi_latest", return_value="4.0.1"),
            patch("meridian.update.do_upgrade", return_value=True),
            patch("meridian.update.verify_installed_version", return_value=True),
        ):
            assert run_self_update() == 0

    def test_upgrade_execution_error_returns_system_exit_code(self) -> None:
        with (
            patch("meridian.__version__", "4.0.0"),
            patch("meridian.update.get_pypi_latest", return_value="4.0.1"),
            patch("meridian.update.do_upgrade", side_effect=OSError("cannot execute")),
        ):
            assert run_self_update() == 3

    def test_installer_success_without_active_version_change_fails(self) -> None:
        with (
            patch("meridian.__version__", "4.0.0"),
            patch("meridian.update.get_pypi_latest", return_value="4.0.1"),
            patch("meridian.update.do_upgrade", return_value=True),
            patch("meridian.update.verify_installed_version", return_value=False),
        ):
            assert run_self_update() == 3


def test_verify_installed_version_checks_resolved_command() -> None:
    with (
        patch("meridian.update.shutil.which", return_value="/tmp/bin/meridian"),
        patch(
            "meridian.update.subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout="meridian 4.0.1\n"),
        ) as run_command,
    ):
        assert verify_installed_version("4.0.1") is True

    assert run_command.call_args.args[0] == ["/tmp/bin/meridian", "--version"]
    assert run_command.call_args.kwargs["timeout"] == 10
    assert run_command.call_args.kwargs["stdin"] is not None


def test_upgrade_continues_when_dormant_manager_does_not_change_active_command() -> None:
    def which(command: str) -> str | None:
        return f"/usr/bin/{command}" if command in {"uv", "pipx"} else None

    with (
        patch("meridian.update.shutil.which", side_effect=which),
        patch(
            "meridian.update.subprocess.run",
            side_effect=[
                SimpleNamespace(returncode=0, stdout="", stderr=""),
                SimpleNamespace(returncode=0, stdout="", stderr=""),
            ],
        ) as installer,
        patch("meridian.update._refresh_symlink"),
        patch("meridian.update.verify_installed_version", side_effect=[False, True]) as verify,
    ):
        assert do_upgrade("4.0.1") is True

    assert [call.args[0][0] for call in installer.call_args_list] == ["uv", "pipx"]
    assert verify.call_count == 2
