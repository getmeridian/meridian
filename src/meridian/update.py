"""Auto-update mechanism via PyPI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

from packaging.version import InvalidVersion, Version

from meridian import config
from meridian.config import GITHUB_REPO, PYPI_JSON_URL, PYPI_PACKAGE, UPDATE_CHECK_INTERVAL
from meridian.console import err_console, info, ok, warn

_RELEASES_URL = f"{GITHUB_REPO}/releases"
_INSTALL_CMD = "curl -sSf https://getmeridian.org/install.sh | bash"


def get_pypi_releases() -> list[Version] | None:
    """Fetch every published, not fully yanked version from PyPI (None when unreachable)."""
    try:
        req = urllib.request.Request(PYPI_JSON_URL, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
        releases = data["releases"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    versions: list[Version] = []
    for raw, files in releases.items():
        try:
            version = Version(raw)
        except InvalidVersion:
            continue
        if files and all(item.get("yanked") for item in files):
            continue
        versions.append(version)
    return sorted(versions)


def update_target(current: Version, releases: list[Version], *, major: bool = False) -> Version | None:
    """Return the newest release to move to, or None when already current.

    Stays on the current major unless ``major``. Pre-releases are offered only
    to a pre-release build, and only within its own major (the beta channel).
    """

    def eligible(version: Version) -> bool:
        if version <= current:
            return False
        if version.is_prerelease:
            return current.is_prerelease and version.major == current.major
        return major or version.major == current.major

    return max((version for version in releases if eligible(version)), default=None)


def newer_major(current: Version, releases: list[Version]) -> Version | None:
    """Return the newest stable release of a later major version, if any."""
    later = [v for v in releases if v.major > current.major and not v.is_prerelease]
    return max(later, default=None)


def _should_check() -> bool:
    """Return True if enough time has passed since last check."""
    check_file = config.CACHE_DIR / "last_update_check"
    try:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if check_file.exists():
            last_check = int(check_file.read_text().strip() or "0")
            if time.time() - last_check < UPDATE_CHECK_INTERVAL:
                return False
        check_file.write_text(str(int(time.time())))
    except ValueError:
        try:
            check_file.write_text(str(int(time.time())))
        except OSError:
            return False
    except OSError:
        # Update notifications are optional and must never break a command.
        return False
    return True


def check_for_update(current_version: str) -> None:
    """Check PyPI for updates and surface them without auto-upgrading."""
    if not _should_check():
        return

    releases = get_pypi_releases()
    if not releases:
        return
    try:
        current = Version(current_version)
    except InvalidVersion:
        return

    target = update_target(current, releases)
    if target is not None:
        if target.is_prerelease:
            kind = "Beta"
        else:
            kind = "Patch release" if target.release[:2] == current.release[:2] else "See what's new"
        err_console.print(f"\n  [warn]v{target} available[/warn]")
        err_console.print(f"  [dim]{kind}: {_RELEASES_URL}[/dim]")
        err_console.print(
            "  [dim]Run[/dim] [bold]meridian update[/bold] [dim]when ready, then[/dim] "
            "[bold]meridian apply[/bold] [dim]to bring your servers up to date[/dim]\n"
        )
        return

    later = newer_major(current, releases)
    if later is not None:
        err_console.print(f"\n  [warn]Meridian {later.major} is available[/warn] [dim](major release)[/dim]")
        err_console.print(f"  [dim]Read the release notes before moving: {_RELEASES_URL}[/dim]")
        err_console.print("  [dim]Then run[/dim] [bold]meridian update --major[/bold]\n")


def do_upgrade(expected_version: str = "") -> bool:
    """Try each available installer until the active command is upgraded."""
    # An exact pin keeps the chosen major and lets installers accept a pre-release.
    spec = f"{PYPI_PACKAGE}=={expected_version}" if expected_version else PYPI_PACKAGE
    commands: list[list[str]] = []
    if shutil.which("uv"):
        commands.append(
            ["uv", "tool", "install", "--force", spec] if expected_version else ["uv", "tool", "upgrade", spec]
        )
    if shutil.which("pipx"):
        commands.append(["pipx", "install", "--force", spec] if expected_version else ["pipx", "upgrade", spec])
    if shutil.which("pip3"):
        commands.extend(
            [
                ["pip3", "install", "--upgrade", "--user", spec],
                ["pip3", "install", "--upgrade", "--user", "--break-system-packages", spec],
            ]
        )

    for command in commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode != 0:
            continue
        _refresh_symlink()
        if not expected_version or verify_installed_version(expected_version):
            return True
    return False


def verify_installed_version(expected: str) -> bool:
    """Prove that the command users will invoke resolves to the upgraded version."""
    meridian_bin = shutil.which("meridian")
    if not meridian_bin:
        return False
    try:
        result = subprocess.run(
            [meridian_bin, "--version"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=10,
            env={**os.environ, "MERIDIAN_DISABLE_UPDATE_CHECK": "1"},
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    rendered = result.stdout.strip().removeprefix("meridian ").strip()
    try:
        return Version(rendered) == Version(expected)
    except InvalidVersion:
        return False


def _refresh_symlink() -> None:
    """Re-create /usr/local/bin/meridian symlink after upgrade."""
    meridian_bin = shutil.which("meridian")
    if not meridian_bin or meridian_bin == "/usr/local/bin/meridian":
        return
    symlink = Path("/usr/local/bin/meridian")
    if not symlink.is_symlink():
        return
    # Only refresh if the symlink already exists (install.sh created it)
    try:
        subprocess.run(
            ["sudo", "-n", "ln", "-sf", meridian_bin, "/usr/local/bin/meridian"],
            capture_output=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


def run_self_update(major: bool = False) -> int:
    """Explicit self-update command; stays on the current major unless ``major``."""
    info("Checking for updates...")
    releases = get_pypi_releases()

    if not releases:
        warn("Could not reach PyPI to check for updates")
        return 3

    from meridian import __version__

    try:
        current = Version(__version__)
    except InvalidVersion:
        warn("Could not parse version numbers")
        return 3

    target = update_target(current, releases, major=major)
    later = newer_major(current, releases)
    if target is None:
        ok(f"Already on the latest {current.major}.x version (v{__version__})")
        if later is not None and not major:
            info(f"Meridian {later.major} is available. Read the release notes first: {_RELEASES_URL}")
            info("Then run `meridian update --major`")
        return 0

    if target.major != current.major:
        warn(f"Moving to Meridian {target.major}. Review the release notes before redeploying:")
        info(_RELEASES_URL)
    elif target.release[:2] != current.release[:2]:
        info(f"v{target} available. What's new: {_RELEASES_URL}")

    latest = str(target)
    info(f"Updating v{__version__} → v{latest}...")
    try:
        upgraded = do_upgrade(latest)
    except (OSError, subprocess.SubprocessError):
        upgraded = False
    if upgraded and verify_installed_version(latest):
        ok(f"Updated to v{latest}")

        if config.CLUSTER_CONFIG.exists():
            info("Run `meridian fleet upgrade` to roll its pinned images out to your servers")
        else:
            info("Run `meridian deploy` to set up your first server")
        return 0
    if upgraded:
        warn("An installer completed, but the active `meridian` command did not change to the requested version")
    warn("Could not upgrade automatically. Try reinstalling:")
    info(_INSTALL_CMD)
    return 3
