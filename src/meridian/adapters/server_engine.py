"""Runtime SSH adapter for Engine server-onboarding operations."""

from __future__ import annotations

from meridian.core.execution import ServerConnection
from meridian.core.servers import ServerProfile
from meridian.ssh import ServerConnection as SSHServerConnection


def default_server_connection_factory(
    profile: ServerProfile,
    *,
    identity_file: str = "",
    password: str = "",
) -> ServerConnection:
    """Create a non-interactive SSH connection for local Engine operations."""
    return SSHServerConnection(
        profile.host,
        profile.ssh_user,
        port=profile.ssh_port,
        local_mode=False,
        multiplex=not identity_file,
        identity_file=identity_file,
        password=password,
    )
