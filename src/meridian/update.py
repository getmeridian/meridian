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


def _should_check() -> bool:
    """Return True if enough time has passed since last check."""
    check_file = CACHE_DIR / "last_update_check"
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
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
        # Major: review before updating
        err_console.print(f"\n  [bold red]v{latest} available (major release)[/bold red]")
        err_console.print(f"  [dim]Review changes before updating: {_RELEASES_URL}[/dim]")
        err_console.print("  [dim]After updating, run[/dim] [bold]meridian deploy[/bold] [dim]to apply[/dim]\n")
    else:
        # Minor: inform, link to changelog
        err_console.print(f"\n  [warn]v{latest} available[/warn]")
        err_console.print(f"  [dim]See what's new: {_RELEASES_URL}[/dim]")
        err_console.print(
            "  [dim]Run[/dim] [bold]meridian update[/bold] [dim]then[/dim] "
            "[bold]meridian deploy[/bold] [dim]to apply[/dim]\n"
        )


def do_upgrade(expected_version: str = "") -> bool:
    """Try each available installer until the active command is upgraded."""
    commands: list[list[str]] = []
    if shutil.which("uv"):
        commands.append(["uv", "tool", "upgrade", PYPI_PACKAGE])
    if shutil.which("pipx"):
        commands.append(["pipx", "upgrade", PYPI_PACKAGE])
    if shutil.which("pip3"):
        commands.extend(
            [
                ["pip3", "install", "--upgrade", "--user", PYPI_PACKAGE],
                ["pip3", "install", "--upgrade", "--user", "--break-system-packages", PYPI_PACKAGE],
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


def run_self_update() -> int:
    """Explicit self-update command."""
    info("Checking for updates...")
    latest = get_pypi_latest()

    if not latest:
        warn("Could not reach PyPI to check for updates")
        return 3

    from meridian import __version__

    try:
        current = Version(__version__)
        remote = Version(latest)
    except InvalidVersion:
        warn("Could not parse version numbers")
        return 3

    if remote <= current:
        ok(f"Already on the latest version (v{__version__})")
        return 0

    # Version-level context
    if current.major != remote.major:
        warn(f"Major release v{latest} — review changes before redeploying:")
        info(_RELEASES_URL)
    elif current.minor != remote.minor:
        info(f"v{latest} available. What's new: {_RELEASES_URL}")

    info(f"Updating v{__version__} → v{latest}...")
    try:
        upgraded = do_upgrade(latest)
    except (OSError, subprocess.SubprocessError):
        upgraded = False
    if upgraded and verify_installed_version(latest):
        ok(f"Updated to v{latest}")
        info("Run `meridian deploy` to apply changes to your servers")
        return 0
    if upgraded:
        warn("An installer completed, but the active `meridian` command did not change to the requested version")
    warn("Could not upgrade automatically. Try reinstalling:")
    info(_INSTALL_CMD)
    return 3
