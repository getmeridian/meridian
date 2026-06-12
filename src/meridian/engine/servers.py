"""Server onboarding operations for the local Meridian Engine."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from meridian.config import MERIDIAN_SSH_KEY_FILE
from meridian.core.servers import (
    ServerBootstrapKeyRequest,
    ServerBootstrapKeyResult,
    ServerConnectionDraft,
    ServerProfile,
    ServerValidateRequest,
    ServerValidateResult,
    profile_from_draft,
)
from meridian.engine.errors import EngineError


class ServerProfileStoreLike(Protocol):
    """Minimal profile store surface needed by Engine server operations."""

    def list(self) -> list[ServerProfile]:
        """Return known server profiles."""

    def find(self, query: str) -> ServerProfile | None:
        """Find a server by ID, title, or host."""

    def upsert(self, profile: ServerProfile) -> None:
        """Insert or replace a server profile."""


class CommandResultLike(Protocol):
    """Small command-result surface used by Engine server operations."""

    returncode: int
    stdout: str
    stderr: str


class ServerConnectionLike(Protocol):
    """Small subset of ServerConnection used by onboarding operations."""

    def run(
        self,
        command: str,
        timeout: int = 30,
        *,
        sudo: bool | None = None,
        sensitive: bool = False,
    ) -> CommandResultLike:
        """Run a command on the server."""


class ServerConnectionFactory(Protocol):
    """Construct SSH connections, optionally pinned to a specific private key."""

    def __call__(
        self,
        profile: ServerProfile,
        *,
        identity_file: str = "",
        password: str = "",
    ) -> ServerConnectionLike:
        """Build a connection for a saved server profile."""


@dataclass(frozen=True)
class KeyMaterial:
    """Public key material plus the local private key path, when Meridian owns it."""

    private_key_path: str
    public_key: str


class KeyProvider(Protocol):
    """Return or create key material for key-bootstrap operations."""

    def __call__(self) -> KeyMaterial:
        """Return key material."""


def _missing_server_connection_factory(
    profile: ServerProfile,
    *,
    identity_file: str = "",
    password: str = "",
) -> ServerConnectionLike:
    raise EngineError(
        "No server connection adapter configured",
        hint="Start through `meridian studio` or pass a ServerConnectionFactory.",
        category="bug",
    )


def ensure_meridian_keypair(path: Path = MERIDIAN_SSH_KEY_FILE) -> KeyMaterial:
    """Create or reuse Meridian's dedicated local SSH key pair."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    public_path = path.with_suffix(path.suffix + ".pub")

    if not path.exists():
        try:
            subprocess.run(
                [
                    "ssh-keygen",
                    "-t",
                    "ed25519",
                    "-N",
                    "",
                    "-f",
                    str(path),
                    "-C",
                    "meridian-local-engine",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
                stdin=subprocess.DEVNULL,
            )
            path.chmod(0o600)
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise EngineError(
                "Could not create Meridian SSH key",
                hint="Install OpenSSH client tools, then try server key setup again.",
                category="system",
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
            raise EngineError(
                "Could not read Meridian SSH public key",
                hint=f"Check the key file permissions: {path}",
                category="system",
            ) from exc

    return KeyMaterial(private_key_path=str(path), public_key=public_path.read_text(encoding="utf-8").strip())


def save_server_profile(draft: ServerConnectionDraft, store: ServerProfileStoreLike) -> ServerProfile:
    """Persist a user-shaped server draft as an Engine-owned server profile."""
    profile = profile_from_draft(draft, source="engine")
    store.upsert(profile)
    return profile


def validate_server_connection(
    request: ServerValidateRequest,
    store: ServerProfileStoreLike,
    *,
    connection_factory: ServerConnectionFactory = _missing_server_connection_factory,
) -> ServerValidateResult:
    """Validate SSH connectivity for a saved server or draft and persist the result."""
    profile = _profile_from_validate_request(request, store)
    conn = connection_factory(profile)

    probe = conn.run("printf 'meridian-ok\\n'", timeout=10, sudo=False)
    if probe.returncode != 0:
        message = _ssh_failure_message(probe)
        failed = _with_validation_state(profile, auth_state="failed", last_error=message)
        store.upsert(failed)
        return ServerValidateResult(
            server=failed,
            reachable=_looks_network_reachable(probe),
            auth_ok=False,
            sudo_ok=False,
            hints=_ssh_failure_hints(profile, probe),
        )

    detected_os = _detect_os(conn)
    sudo_ok = _check_sudo(profile, conn)
    hints = [] if sudo_ok else [_sudo_hint(profile)]
    validated = _with_validation_state(
        profile,
        auth_state="validated" if sudo_ok else "failed",
        last_error="" if sudo_ok else _sudo_hint(profile),
    )
    store.upsert(validated)
    return ServerValidateResult(
        server=validated,
        reachable=True,
        auth_ok=True,
        sudo_ok=sudo_ok,
        detected_os=detected_os,
        hints=hints,
    )


def bootstrap_server_key(
    request: ServerBootstrapKeyRequest,
    store: ServerProfileStoreLike,
    *,
    connection_factory: ServerConnectionFactory = _missing_server_connection_factory,
    key_provider: KeyProvider = ensure_meridian_keypair,
    password: str = "",
) -> ServerBootstrapKeyResult:
    """Install a public SSH key on a saved server using an already-valid SSH session."""
    profile = _require_saved_profile(request.server_ref, store)
    if request.key_policy == "generate_meridian":
        key = key_provider()
        public_key = _normalize_public_key(key.public_key)
        key_path = key.private_key_path
    else:
        public_key = _normalize_public_key(request.public_key)
        key_path = ""

    if password and not host_key_known(profile.host, profile.ssh_port):
        raise EngineError(
            "SSH host key is not trusted yet",
            hint=(
                "Before sending the one-time SSH password, verify the server fingerprint from your VPS provider. "
                f"Then run `{_ssh_command(profile)}` once and accept it only if it matches."
            ),
            category="user",
        )

    conn = connection_factory(profile, password=password)
    install = conn.run(_install_public_key_command(public_key), timeout=20, sudo=False, sensitive=True)
    if install.returncode != 0:
        message = _ssh_failure_message(install)
        failed = _with_validation_state(profile, auth_state="failed", last_error=message)
        store.upsert(failed)
        raise EngineError(
            "Could not install SSH public key",
            hint="\n".join(_ssh_failure_hints(profile, install)),
            category="system",
        )

    warnings: list[str] = []
    verified = _verify_key_login(profile, connection_factory, key_path)
    if not verified:
        warnings.append("The public key was installed, but Meridian could not verify login with that key yet.")
    if request.disable_password_auth:
        warnings.append("Password-auth hardening is handled by deploy after key login is verified.")

    updated = _with_validation_state(
        profile,
        auth_state="key_ready" if verified else "validated",
        key_path=key_path or profile.key_path,
        last_error="" if verified else "Key verification did not complete.",
    )
    store.upsert(updated)
    return ServerBootstrapKeyResult(
        server=updated,
        key_policy=request.key_policy,
        key_path=key_path,
        installed=True,
        verified=verified,
        password_auth_disabled=False,
        warnings=warnings,
    )


def _profile_from_validate_request(request: ServerValidateRequest, store: ServerProfileStoreLike) -> ServerProfile:
    if request.draft is not None:
        return profile_from_draft(request.draft, source="engine")
    return _require_saved_profile(request.server_ref, store)


def _require_saved_profile(server_ref: str, store: ServerProfileStoreLike) -> ServerProfile:
    profile = store.find(server_ref)
    if profile is None:
        raise EngineError(
            f"Server '{server_ref}' not found",
            hint="Add the server first, or choose an existing server from the list.",
            category="user",
        )
    return profile


def _with_validation_state(
    profile: ServerProfile,
    *,
    auth_state: str,
    last_error: str,
    key_path: str | None = None,
) -> ServerProfile:
    updates = {
        "auth_state": auth_state,
        "last_validated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "last_error": last_error,
    }
    if key_path is not None:
        updates["key_path"] = key_path
    return profile.model_copy(update=updates)


def _detect_os(conn: ServerConnectionLike) -> str:
    result = conn.run(
        '. /etc/os-release 2>/dev/null && printf \'%s %s\\n\' "$ID" "$VERSION_ID" || uname -s',
        timeout=10,
        sudo=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _check_sudo(profile: ServerProfile, conn: ServerConnectionLike) -> bool:
    if profile.ssh_user == "root":
        return True
    result = conn.run("sudo -n true", timeout=10, sudo=False)
    return result.returncode == 0


def _verify_key_login(
    profile: ServerProfile,
    connection_factory: ServerConnectionFactory,
    key_path: str,
) -> bool:
    if not key_path:
        return False
    result = connection_factory(profile, identity_file=key_path).run(
        "printf 'meridian-key-ok\\n'",
        timeout=10,
        sudo=False,
    )
    return result.returncode == 0


def _install_public_key_command(public_key: str) -> str:
    quoted_key = shlex.quote(public_key)
    return (
        "umask 077; "
        "mkdir -p ~/.ssh; "
        "touch ~/.ssh/authorized_keys; "
        f"grep -qxF {quoted_key} ~/.ssh/authorized_keys || "
        f"printf '%s\\n' {quoted_key} >> ~/.ssh/authorized_keys; "
        "chmod 700 ~/.ssh; "
        "chmod 600 ~/.ssh/authorized_keys"
    )


def _normalize_public_key(public_key: str) -> str:
    normalized = public_key.strip()
    if not normalized:
        raise EngineError("Public SSH key is required", hint="Paste a .pub key, not a private key.", category="user")
    if "\n" in normalized or "\r" in normalized:
        raise EngineError("Public SSH key must be one line", hint="Paste one authorized_keys line.", category="user")
    if "PRIVATE KEY" in normalized:
        raise EngineError("Do not paste a private key", hint="Paste the matching .pub file instead.", category="user")
    return normalized


def _ssh_failure_message(result: CommandResultLike) -> str:
    text = (result.stderr or result.stdout or "").strip()
    if not text:
        return f"SSH command failed with exit code {result.returncode}."
    return text.splitlines()[0][:240]


def _looks_network_reachable(result: CommandResultLike) -> bool:
    text = f"{result.stderr}\n{result.stdout}".lower()
    return any(fragment in text for fragment in ("permission denied", "host key verification failed", "identification"))


def _ssh_failure_hints(profile: ServerProfile, result: CommandResultLike) -> list[str]:
    text = f"{result.stderr}\n{result.stdout}".lower()
    if "remote host identification has changed" in text:
        return [
            "The SSH host key changed. If you rebuilt this server, remove the old key with "
            f"`ssh-keygen -R {shlex.quote(profile.host)}` and validate again."
        ]
    if "host key verification failed" in text or "known_hosts" in text:
        return [
            "Verify the SSH fingerprint in your VPS provider console, then trust it from Terminal with "
            f"`{_ssh_command(profile)}`. "
            f"OpenSSH stores it as {host_key_lookup(profile.host, profile.ssh_port)!r}."
        ]
    if "permission denied" in text or "publickey" in text:
        return [
            "SSH reached the server, but key authentication failed.",
            f"Use password login once to install a key: `{_ssh_copy_id_command(profile)}`.",
        ]
    if "connection refused" in text:
        return [f"SSH is not accepting connections on port {profile.ssh_port}. Check the VPS SSH port or firewall."]
    if "timed out" in text or "no route to host" in text or result.returncode == 124:
        return ["Meridian could not reach this IP and port. Check the IP address, server status, and firewall."]
    if "could not resolve hostname" in text:
        return ["Check the server IP address."]
    return [f"Test manually from a terminal: `{_ssh_command(profile)}`."]


def _sudo_hint(profile: ServerProfile) -> str:
    return (
        f"User {profile.ssh_user!r} can SSH, but needs passwordless sudo for deploy. "
        "Use root, or add this user to sudoers before continuing."
    )


def _ssh_command(profile: ServerProfile) -> str:
    target = _ssh_target(profile)
    port = f" -p {profile.ssh_port}" if profile.ssh_port != 22 else ""
    return f"ssh{port} {shlex.quote(target)}"


def _ssh_copy_id_command(profile: ServerProfile) -> str:
    target = _ssh_target(profile)
    port = f" -p {profile.ssh_port}" if profile.ssh_port != 22 else ""
    return f"ssh-copy-id{port} {shlex.quote(target)}"


def host_key_known(host: str, port: int = 22) -> bool:
    """Check OpenSSH known_hosts without importing the SSH runtime adapter."""
    lookup = host_key_lookup(host, port)
    try:
        result = subprocess.run(
            ["ssh-keygen", "-F", lookup],
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def host_key_lookup(host: str, port: int = 22) -> str:
    """Return the OpenSSH known_hosts lookup string for a host/port."""
    return f"[{host}]:{port}" if port != 22 else host


def _ssh_target(profile: ServerProfile) -> str:
    host = f"[{profile.host}]" if ":" in profile.host and not profile.host.startswith("[") else profile.host
    return f"{profile.ssh_user}@{host}"
