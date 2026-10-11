"""Auto-update mechanism via PyPI."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

from packaging.version import InvalidVersion, Version

from meridian.config import (
    CACHE_DIR,
    GITHUB_REPO,
    PYPI_JSON_URL,
    PYPI_PACKAGE,
    UPDATE_CHECK_INTERVAL,
)
from meridian.console import err_console, info, ok, warn

_RELEASES_URL = f"{GITHUB_REPO}/releases"
_INSTALL_CMD = "curl -sSf https://getmeridian.org/install.sh | bash"


def get_pypi_latest() -> str | None:
    """Fetch latest version from PyPI JSON API."""
    try:
        req = urllib.request.Request(PYPI_JSON_URL, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            return data["info"]["version"]
    except (OSError, ValueError, KeyError):
        return None


def get_pypi_releases() -> list[Version]:
    """Fetch every published, non-prerelease version from PyPI (empty on failure)."""
    try:
        req = urllib.request.Request(PYPI_JSON_URL, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
    except (OSError, ValueError):
        return []
    versions: list[Version] = []
    for raw, files in data.get("releases", {}).items():
        try:
            version = Version(raw)
        except InvalidVersion:
            continue
        if version.is_prerelease or (files and all(item.get("yanked") for item in files)):
            continue
        versions.append(version)
    return sorted(versions)


def latest_in_major(releases: list[Version], major: int) -> Version | None:
    """Return the newest release that keeps the given major version."""
    candidates = [version for version in releases if version.major == major]
    return candidates[-1] if candidates else None


def _should_check() -> bool:
    """Return True if enough time has passed since last check."""
    check_file = CACHE_DIR / "last_update_check"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if check_file.exists():
        try:
            last_check = int(check_file.read_text().strip() or "0")
            if time.time() - last_check < UPDATE_CHECK_INTERVAL:
                return False
        except (ValueError, OSError):
            pass

    check_file.write_text(str(int(time.time())))
    return True


def check_for_update(current_version: str) -> None:
    """Check PyPI for updates and surface them without auto-upgrading."""
    if not _should_check():
        return

    latest = get_pypi_latest()
    if not latest or latest == current_version:
        return

    try:
        current = Version(current_version)
        remote = Version(latest)
    except InvalidVersion:
        return

    if remote <= current:
        return  # running dev/pre-release, don't downgrade

    if current.major == remote.major and current.minor == remote.minor:
        err_console.print(f"\n  [warn]v{latest} available[/warn]")
        err_console.print(f"  [dim]Patch release: {_RELEASES_URL}[/dim]")
        err_console.print(
            "  [dim]Run[/dim] [bold]meridian update[/bold] [dim]when ready, then[/dim] "
            "[bold]meridian deploy[/bold] [dim]to apply[/dim]\n"
        )
    elif current.major != remote.major:
        # Major: not an in-place upgrade; existing servers keep working.
        err_console.print(f"\n  [warn]Meridian {remote.major} is available[/warn] [dim](major release)[/dim]")
        err_console.print("  [dim]It is not an in-place upgrade; your servers keep working on this version.[/dim]")
        err_console.print(
            f"  [dim]Meridian {current.major}.x will eventually stop receiving updates, so plan your move.[/dim]"
        )
        err_console.print(f"  [dim]Read before moving: {_RELEASES_URL}[/dim]\n")
    else:
        # Minor: inform, link to changelog
        err_console.print(f"\n  [warn]v{latest} available[/warn]")
        err_console.print(f"  [dim]See what's new: {_RELEASES_URL}[/dim]")
        err_console.print(
            "  [dim]Run[/dim] [bold]meridian update[/bold] [dim]then[/dim] "
            "[bold]meridian deploy[/bold] [dim]to apply[/dim]\n"
        )


def do_upgrade(version: str = "") -> bool:
    """Upgrade via uv > pipx > pip3, to ``version`` when given, else the latest."""
    success = False
    spec = f"{PYPI_PACKAGE}=={version}" if version else PYPI_PACKAGE

    if shutil.which("uv"):
        command = ["uv", "tool", "install", "--force", spec] if version else ["uv", "tool", "upgrade", PYPI_PACKAGE]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode == 0:
            success = True

    if not success and shutil.which("pipx"):
        command = ["pipx", "install", "--force", spec] if version else ["pipx", "upgrade", PYPI_PACKAGE]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode == 0:
            success = True

    if not success and shutil.which("pip3"):
        # Try --user first, then --break-system-packages for PEP 668
        for extra_args in [["--user"], ["--user", "--break-system-packages"]]:
            result = subprocess.run(
                ["pip3", "install", "--upgrade", *extra_args, spec],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                success = True
                break

    if success:
        _refresh_symlink()

    return success


def _explain_major_release(current: Version, remote: Version) -> None:
    warn(f"Meridian {remote.major} is available, but it is not an in-place upgrade.")
    info("Your servers keep working on this version. Meridian 4 uses a new panel,")
    info("so moving means a fresh deploy and new connection links for every user.")
    info(f"Meridian {current.major}.x will eventually stop receiving updates, so plan your move.")
    info(f"Read the release notes, then run `meridian update --major`: {_RELEASES_URL}")


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


def run_self_update(major: bool = False) -> None:
    """Explicit self-update command; stays on the current major unless ``major``."""
    info("Checking for updates...")
    latest = get_pypi_latest()

    if not latest:
        warn("Could not reach PyPI to check for updates")
        return

    from meridian import __version__

    try:
        current = Version(__version__)
        remote = Version(latest)
    except InvalidVersion:
        warn("Could not parse version numbers")
        return

    if remote <= current:
        ok(f"Already on the latest version (v{__version__})")
        return

    target = ""
    if remote.major > current.major and not major:
        same_major = latest_in_major(get_pypi_releases(), current.major)
        _explain_major_release(current, remote)
        if same_major is None or same_major <= current:
            ok(f"You are on the latest {current.major}.x release (v{__version__})")
            return
        target = str(same_major)
        remote = same_major
        latest = target
    elif remote.major > current.major:
        warn(f"Moving to Meridian {remote.major}. It cannot manage servers deployed by this version:")
        info("deploy a fresh server, give your users the new links, then tear down the old server.")
        info(_RELEASES_URL)
    elif current.minor != remote.minor:
        info(f"v{latest} available. What's new: {_RELEASES_URL}")

    info(f"Updating v{__version__} → v{latest}...")
    if do_upgrade(target):
        ok(f"Updated to v{latest}")
        if remote.major == current.major:
            info("Run `meridian deploy` to apply changes to your servers")
    else:
        warn("Could not upgrade automatically. Try reinstalling:")
        info(_INSTALL_CMD)
