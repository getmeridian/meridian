"""Server resolution logic for CLI commands.

Data structures and pure helpers live in ``meridian.resolve``; this module
contains only CLI-specific resolution functions that depend on Rich, Typer,
prompts, or ``console.fail()``.
"""

from __future__ import annotations

from rich.markup import escape

from meridian import resolve as _resolve_lib
from meridian.commands._helpers import fail_meridian_v3_state, has_meridian_v3_state
from meridian.console import confirm, err_console, fail, has_tty, info, is_json_mode, ok, prompt_secret, warn
from meridian.core.errors import LocalStateError, MeridianError
from meridian.core.inputs import is_ip
from meridian.resolve import ResolvedServer
from meridian.resolve import (
    ensure_server_connection as _ensure_server_connection,
)
from meridian.servers import ServerRegistry, address_is_local
from meridian.ssh import ServerConnection, SSHError, SSHPasswordOnlyError
from meridian.ssh_ui import RichSSHUI

__all__ = [
    "ensure_server_connection",
    "resolve_server",
]


def _resolve_server(
    registry: ServerRegistry,
    explicit_ip: str = "",
    user: str = "",
    port: int = 0,
) -> ResolvedServer:
    """Resolve which server to target.

    The target is parsed like ssh:
    ``[user@]host[:port]``, a saved name, or ``local``. Priority: that target >
    local mode (root) > single registered > fail.

    If user is empty, it's auto-resolved from the server registry.
    If user is explicitly set, it overrides the registry value.
    If port is 0, it's auto-resolved from the server registry (default 22).
    If port is explicitly set (non-zero), it overrides the registry value.
    """
    try:
        target = _resolve_lib.merge_target(explicit_ip, user=user, port=port)
    except ValueError as exc:
        fail(str(exc), hint="Use IP, saved name, user@host[:port], or 'local'.", hint_type="user")
    user, port = target.user, target.port

    ip = ""
    registry_user = ""
    registry_port = 22
    registry_key_path = ""
    local_mode = False

    # 1. 'local' keyword: run on this machine without SSH
    if target.local:
        here = _resolve_lib.local_entry(registry)
        detected_ip = here.host if here else _resolve_lib.detect_public_ip()
        if not detected_ip:
            fail(
                "Could not detect this server's public IP",
                hint="Provide the IP explicitly: meridian <command> 198.51.100.10",
                hint_type="system",
            )
        ip = detected_ip
        local_mode = True
        info(f"Local mode: using this server ({ip})")

    # 2. Named target: a saved server (by name, IP, or ID), else a literal address
    elif target.host:
        entry = registry.find(target.host)
        if entry:
            ip = entry.host
            registry_user = entry.user
            registry_port = entry.port
            registry_key_path = entry.key_path
            local_mode = entry.runs_here()
        elif is_ip(target.host) or "." in target.host:
            ip = target.host
            local_mode = address_is_local(ip)
        else:
            fail(
                f"Server '{target.host}' not found",
                hint="See saved servers: meridian server list",
                hint_type="user",
            )

    # 3. Running on a saved server itself (its recorded machine ID matches)
    else:
        here = _resolve_lib.local_entry(registry)
        if here:
            ip = here.host
            registry_user = here.user
            registry_port = here.port
            local_mode = True
            info(f"Using this server: {here.name or ip}")

        # 4. Single server auto-select
        else:
            selectable_entries = _resolve_lib.auto_selectable_entries(registry)
            if len(selectable_entries) == 1:
                entry = selectable_entries[0]
                ip = entry.host
                registry_user = entry.user
                registry_port = entry.port
                registry_key_path = entry.key_path
                label = f"{entry.name} ({ip})" if entry.name and entry.name != ip else ip
                info(f"Using server: {label}")

            elif len(selectable_entries) > 1:
                err_console.print("\n  Several servers are saved. Name one:\n")
                for entry in selectable_entries:
                    label = entry.name or entry.host
                    err_console.print(
                        f"    [info]{escape(label):<15s}[/info]  {escape(entry.host)}  ({escape(entry.user)})"
                    )
                err_console.print()
                fail(
                    "Choose a server",
                    hint="Example: meridian <command> NAME",
                    hint_type="user",
                )

            else:
                if has_meridian_v3_state():
                    fail_meridian_v3_state()
                fail("No servers configured", hint="Deploy a server first: meridian deploy IP", hint_type="user")

    # Resolve user: explicit flag > registry > this account when local (so the saved
    # profile SSHes in as it from elsewhere) > default root
    resolved_user = user or registry_user or (_resolve_lib.current_login() if local_mode else "") or "root"

    if not _resolve_lib.VALID_SSH_USER.match(resolved_user):
        fail(
            f"SSH user '{resolved_user}' is invalid",
            hint="Use letters, numbers, dots, hyphens, and underscores.",
            hint_type="user",
        )

    # Resolve port: explicit flag > registry > default 22
    resolved_port = port if port else registry_port
    resolved_key_path = registry_key_path if registry_key_path and (not user or user == registry_user) else ""

    conn = ServerConnection(
        ip=ip,
        user=resolved_user,
        local_mode=local_mode,
        port=resolved_port,
        identity_file="" if local_mode else resolved_key_path,
        multiplex=not resolved_key_path,
    )

    return ResolvedServer(
        ip=ip,
        user=resolved_user,
        local_mode=local_mode,
        conn=conn,
    )


def resolve_server(
    registry: ServerRegistry,
    explicit_ip: str = "",
    user: str = "",
    port: int = 0,
) -> ResolvedServer:
    """Resolve a target and render unsafe local-state failures consistently."""
    try:
        return _resolve_server(
            registry,
            explicit_ip=explicit_ip,
            user=user,
            port=port,
        )
    except LocalStateError as exc:
        fail(exc)


def ensure_server_connection(resolved: ResolvedServer, *, offer_key_setup: bool = False) -> ResolvedServer:
    """CLI wrapper — delegates to ``meridian.resolve`` with Rich SSH UI.

    Catches ``SSHError`` from the library layer and converts it to a
    ``fail()`` exit with the appropriate hint. With ``offer_key_setup``, a
    password-only server gets an opt-in offer to install Meridian's own key;
    without a terminal, or in JSON mode, it fails with the manual hint instead.
    The returned target may then be pinned to that key: save
    ``resolved.conn.identity_file`` as the profile's key path.
    """

    try:
        return _ensure_server_connection(resolved, ui=RichSSHUI())
    except SSHPasswordOnlyError as exc:
        if not offer_key_setup or is_json_mode() or not has_tty():
            fail(exc)
        refused = exc
    except SSHError as exc:
        fail(exc)
    keyed = _set_up_meridian_key(resolved, refused)
    try:
        return _ensure_server_connection(keyed, ui=RichSSHUI())
    except SSHError as exc:
        fail(exc)


def _set_up_meridian_key(resolved: ResolvedServer, refused: SSHPasswordOnlyError) -> ResolvedServer:
    """Ask before installing Meridian's key with a one-time password; any refusal keeps the manual hint."""
    warn(f"{resolved.user}@{resolved.ip} does not accept your SSH keys.")
    if not confirm("This server accepts only a password. Create a key for Meridian and install it now?", default=False):
        fail(refused)
    password = prompt_secret(f"SSH password for {resolved.user}@{resolved.ip} (used once, never saved)")
    if not password:
        fail(refused)
    try:
        keyed = _resolve_lib.install_meridian_key(resolved, password)
    except MeridianError as exc:
        fail(exc)
    finally:
        del password
    ok(f"Installed Meridian's SSH key ({keyed.conn.identity_file})")
    return keyed
