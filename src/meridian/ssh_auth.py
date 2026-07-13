"""SSH authentication, UI protocol, and lifecycle helpers.

Extracted from ``ssh.py`` to keep the transport module focused on
``ServerConnection`` and ``CommandResult``.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Protocol

from meridian.ssh_keys import host_key_known

logger = logging.getLogger("meridian.ssh")


# UI callback protocol -- decouples transport from presentation


class SSHUI(Protocol):
    """Callback interface for SSH user-facing output.

    The default implementation logs via the ``meridian.ssh`` logger.
    Pass a Rich-based implementation from the CLI layer to get
    coloured terminal output identical to the previous behaviour.
    """

    def info(self, msg: str) -> None: ...
    def ok(self, msg: str) -> None: ...
    def warn(self, msg: str) -> None: ...
    def host_key_prompt(self, ip: str, fingerprint: str, algo: str) -> bool:
        """Display host-key fingerprint and ask the user to accept.

        Returns ``True`` if the user accepts the key, ``False`` otherwise.
        Raises ``SSHError`` if no interactive terminal is available.
        """
        ...

    def ssh_failed(self, ip: str, user: str, stderr: str) -> None: ...
    def host_key_changed(self, ip: str) -> None: ...


class _LogUI:
    """Default SSHUI that writes to the ``meridian.ssh`` logger."""

    def info(self, msg: str) -> None:
        logger.info(msg)

    def ok(self, msg: str) -> None:
        logger.info(msg)

    def warn(self, msg: str) -> None:
        logger.warning(msg)

    def host_key_prompt(self, ip: str, fingerprint: str, algo: str) -> bool:
        """Prompt via /dev/tty (OS-level, no Rich dependency)."""
        from meridian.ssh import SSHError

        lines = [f"First connection to {ip}"]
        if fingerprint:
            lines.append(f"Host key fingerprint: {fingerprint}")
        else:
            lines.append(f"Host key type: {algo}")
        lines.append("Verify this matches your VPS provider's console.")
        lines.append("A mismatch may indicate a network attack.")
        for ln in lines:
            logger.info(ln)

        try:
            with open("/dev/tty") as tty:
                print("\n  -> Trust this host key? [Y/n] ", end="", flush=True)  # noqa: T201
                answer = tty.readline().strip().lower()
        except OSError:
            raise SSHError(
                f"Cannot verify host key for {ip} (no terminal available)",
                hint="Run interactively, or pre-add the key: ssh-keyscan IP >> ~/.ssh/known_hosts",
                category="user",
            )
        return answer in ("", "y", "yes")

    def ssh_failed(self, ip: str, user: str, stderr: str) -> None:
        logger.error("SSH connection failed: %s", stderr)
        logger.info("1. Copy your SSH key:  ssh-copy-id %s@%s", user, ip)
        logger.info("2. Test manually:      ssh %s@%s", user, ip)
        logger.info("3. Different user:     meridian deploy IP --user ubuntu")

    def host_key_changed(self, ip: str) -> None:
        logger.error("Host key for %s has CHANGED!", ip)
        logger.warning("This could indicate a network attack (MitM).")
        logger.info("If you recently rebuilt this server, remove the old key:")
        logger.info("  ssh-keygen -R %s", ip)


_DEFAULT_UI = _LogUI()


# SSH lifecycle helpers


def ensure_multiplex_dir() -> None:
    """Create the SSH control socket directory if it doesn't exist."""
    sock_dir = Path.home() / ".meridian" / "ssh"
    sock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)


def ensure_askpass_script() -> Path:
    """Create the local askpass helper used for short-lived password SSH."""
    script = Path.home() / ".meridian" / "ssh" / "askpass.sh"
    script.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    script.write_text("#!/bin/sh\nprintf '%s\\n' \"$MERIDIAN_SSH_PASSWORD\"\n", encoding="utf-8")
    script.chmod(0o700)
    return script


def _host_key_known(ip: str, port: int = 22) -> bool:
    """Thin wrapper so test patches on ``meridian.ssh._host_key_known`` keep working."""
    return host_key_known(ip, port)


def _verify_host_key(ip: str, port: int = 22, *, ui: SSHUI | None = None) -> bool:
    """Scan, display, and prompt user to verify the SSH host key.

    Returns True if the user accepts (key added to known_hosts), False otherwise.
    Uses ssh-keyscan to fetch the key and ssh-keygen to compute the fingerprint.
    """
    if ui is None:
        ui = _DEFAULT_UI

    # Scan the host key
    keyscan_cmd = ["ssh-keyscan", "-T", "5"]
    if port != 22:
        keyscan_cmd.extend(["-p", str(port)])
    keyscan_cmd.append(ip)
    try:
        result = subprocess.run(
            keyscan_cmd,
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
        if result.returncode != 0 or not result.stdout.strip():
            ui.warn(f"Could not scan host key for {ip}")
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError):
        ui.warn(f"Could not scan host key for {ip}")
        return False

    key_lines = [line for line in result.stdout.strip().splitlines() if line and not line.startswith("#")]
    if not key_lines:
        ui.warn(f"No host keys found for {ip}")
        return False

    # Prefer ed25519 > ecdsa > rsa
    preferred = None
    for pref in ("ssh-ed25519", "ecdsa-sha2", "ssh-rsa"):
        for line in key_lines:
            if pref in line:
                preferred = line
                break
        if preferred:
            break
    if not preferred:
        preferred = key_lines[0]

    # Compute fingerprint
    try:
        result = subprocess.run(
            ["ssh-keygen", "-lf", "-"],
            input=preferred,
            capture_output=True,
            text=True,
            timeout=5,
        )
        fingerprint = result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        fingerprint = ""

    # Extract algorithm for display
    key_type = preferred.split()[1] if len(preferred.split()) >= 2 else "unknown"
    parts = preferred.split()
    algo = parts[1] if len(parts) >= 3 else key_type

    # Prompt user via UI callback (handles display + TTY interaction)
    accepted = ui.host_key_prompt(ip, fingerprint, algo)
    if not accepted:
        return False

    # Add only the verified key to known_hosts (user only saw this fingerprint)
    known_hosts = Path.home() / ".ssh" / "known_hosts"
    known_hosts.parent.mkdir(mode=0o700, exist_ok=True)
    with open(known_hosts, "a") as f:
        f.write(preferred + "\n")

    ui.ok("Host key saved")
    return True
