"""Saved server profile contracts shared by the server shelf and the CLI."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field

from meridian.core.inputs import (
    IPAddressValue,
    PortValue,
    RequiredNameValue,
    ServerTitleValue,
    SshUserValue,
)
from meridian.core.models import CoreModel

ServerAuthState = Literal["unknown", "validated", "key_ready", "failed"]
# "engine" stays valid: profiles an earlier local UI saved still load.
ServerSource = Literal["manual", "engine"]
# systemd's /etc/machine-id: 32 lowercase hex digits, or empty when unknown.
MachineIdValue = Annotated[str, Field(pattern=r"^([0-9a-f]{32})?$")]


class ServerConnectionDraft(CoreModel):
    """Unsaved server connection details before they become a saved profile."""

    title: ServerTitleValue
    host: IPAddressValue
    ssh_user: SshUserValue = "root"
    ssh_port: PortValue = 22


class ServerProfile(CoreModel):
    """Saved server profile referenced by deploy, relay, verify, and recovery flows."""

    id: RequiredNameValue
    title: ServerTitleValue
    host: IPAddressValue
    ssh_user: SshUserValue = "root"
    ssh_port: PortValue = 22
    auth_state: ServerAuthState = "unknown"
    last_validated_at: str = ""
    last_error: str = ""
    key_path: str = ""
    source: ServerSource = "manual"
    machine_id: MachineIdValue = Field(
        default="",
        description="The server's /etc/machine-id, recorded when Meridian ran on it. "
        "On that machine Meridian runs commands locally instead of over SSH.",
    )


def server_profile_id(host: str, ssh_user: str = "root", ssh_port: int = 22) -> str:
    """Generate an immutable local ID independent of mutable connection details."""
    del host, ssh_user, ssh_port
    return f"srv-{uuid4().hex[:12]}"


def profile_from_draft(
    draft: ServerConnectionDraft,
    *,
    auth_state: ServerAuthState = "unknown",
    source: ServerSource = "manual",
    profile_id: str = "",
) -> ServerProfile:
    """Convert UX-shaped draft input into a saved profile contract."""
    return ServerProfile(
        id=profile_id or server_profile_id(draft.host, draft.ssh_user, draft.ssh_port),
        title=draft.title,
        host=draft.host,
        ssh_user=draft.ssh_user,
        ssh_port=draft.ssh_port,
        auth_state=auth_state,
        source=source,
    )
