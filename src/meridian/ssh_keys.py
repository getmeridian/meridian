"""Lightweight SSH key utilities (stdlib-only).

Host-key lookups plus Meridian's dedicated client key: create it once,
install its public half in ``authorized_keys``, and check that it logs in.
No Rich, no console, no transport — safe for ``meridian.ssh`` and
``meridian.resolve`` alike.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from meridian import config
from meridian.core.errors import MeridianError
from meridian.core.execution import ServerCommandResult, ServerConnection

KEY_COMMENT = "meridian"


class SSHKeyError(MeridianError):
    """Raised when Meridian's SSH key cannot be created, read, or accepted."""


@dataclass(frozen=True)
class KeyMaterial:
    """Public key material plus the local private key path, when Meridian owns it."""

    private_key_path: str
    public_key: str


def host_key_known(host: str, port: int = 22) -> bool:
    """Check if the host key for *host* is already in ``known_hosts``."""
    lookup = host_key_lookup(host, port)
    try:
        result = subprocess.run(
            ["ssh-keygen", "-F", lookup],
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def host_key_lookup(host: str, port: int = 22) -> str:
    """Return the OpenSSH ``known_hosts`` lookup string for a host/port.

    Non-default ports use ``[host]:port`` notation, matching ``ssh-keygen -F``.
    """
    return f"[{host}]:{port}" if port != 22 else host


def ensure_meridian_keypair(path: Path | None = None) -> KeyMaterial:
    """Create or reuse Meridian's dedicated local SSH key pair."""
    path = path or config.MERIDIAN_SSH_KEY_FILE
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    public_path = path.with_suffix(path.suffix + ".pub")

    if not path.exists():
        try:
            subprocess.run(
                ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(path), "-C", KEY_COMMENT],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
                stdin=subprocess.DEVNULL,
            )
            path.chmod(0o600)
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise SSHKeyError(
                "Could not create Meridian SSH key",
                hint="Install OpenSSH client tools, then try server key setup again.",
            ) from exc

    if not public_path.exists():
        try:
            result = subprocess.run(
                ["ssh-keygen", "-y", "-f", str(path)],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
                stdin=subprocess.DEVNULL,
            )
            public_path.write_text(result.stdout.strip() + "\n", encoding="utf-8")
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise SSHKeyError(
                "Could not read Meridian SSH public key",
                hint=f"Check the key file permissions: {path}",
            ) from exc

    return KeyMaterial(private_key_path=str(path), public_key=public_path.read_text(encoding="utf-8").strip())


def normalize_public_key(public_key: str) -> str:
    """Return one ``authorized_keys`` line, refusing empty, multi-line, or private key text."""
    normalized = public_key.strip()
    if not normalized:
        raise SSHKeyError("Public SSH key is required", hint="Paste a .pub key, not a private key.", category="user")
    if "\n" in normalized or "\r" in normalized:
        raise SSHKeyError("Public SSH key must be one line", hint="Paste one authorized_keys line.", category="user")
    if "PRIVATE KEY" in normalized:
        raise SSHKeyError("Do not paste a private key", hint="Paste the matching .pub file instead.", category="user")
    return normalized


def install_public_key(conn: ServerConnection, public_key: str) -> ServerCommandResult:
    """Append *public_key* to the login user's ``authorized_keys`` once (as that user, never sudo)."""
    quoted_key = shlex.quote(normalize_public_key(public_key))
    command = (
        "umask 077; "
        "mkdir -p ~/.ssh; "
        "touch ~/.ssh/authorized_keys; "
        f"grep -qxF {quoted_key} ~/.ssh/authorized_keys || "
        f"printf '%s\\n' {quoted_key} >> ~/.ssh/authorized_keys; "
        "chmod 700 ~/.ssh; "
        "chmod 600 ~/.ssh/authorized_keys"
    )
    return conn.run(command, timeout=20, sudo=False, sensitive=True)


def key_login_works(conn: ServerConnection) -> bool:
    """Whether a connection pinned to one private key logs in (no sudo involved)."""
    return conn.run("printf 'meridian-key-ok\\n'", timeout=10, sudo=False).returncode == 0
