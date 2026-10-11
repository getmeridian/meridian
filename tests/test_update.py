"""Tests for auto-update logic."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from meridian.update import _should_check, check_for_update


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


class TestMajorReleaseGuard:
    """`meridian update` must never cross into a new major version by itself."""

    def _run(self, current: str, latest: str, releases: list[str], *, major: bool = False):
        from packaging.version import Version

        from meridian import update

        with (
            patch("meridian.__version__", current),
            patch.object(update, "get_pypi_latest", return_value=latest),
            patch.object(update, "get_pypi_releases", return_value=[Version(v) for v in releases]),
            patch.object(update, "do_upgrade", return_value=True) as do_upgrade,
            patch.object(update, "err_console"),
        ):
            update.run_self_update(major=major)
        return do_upgrade

    def test_stays_on_the_current_major_line(self) -> None:
        do_upgrade = self._run("3.17.2", "4.0.0", ["3.17.2", "3.17.3", "4.0.0"])

        do_upgrade.assert_called_once_with("3.17.3")

    def test_already_latest_in_major_does_not_upgrade(self) -> None:
        do_upgrade = self._run("3.17.3", "4.0.0", ["3.17.3", "4.0.0"])

        do_upgrade.assert_not_called()

    def test_major_flag_moves_to_the_new_major(self) -> None:
        do_upgrade = self._run("3.17.3", "4.0.0", ["3.17.3", "4.0.0"], major=True)

        do_upgrade.assert_called_once_with("")

    def test_latest_in_major_ignores_other_majors(self) -> None:
        from packaging.version import Version

        from meridian.update import latest_in_major

        assert latest_in_major([Version("3.1.0"), Version("3.17.3"), Version("4.0.0")], 3) == Version("3.17.3")
        assert latest_in_major([Version("4.0.0")], 3) is None
