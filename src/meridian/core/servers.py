"""Server onboarding contracts for Studio, Engine, and CLI adapters."""

from __future__ import annotations

import hashlib
from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from meridian.core.inputs import (
    IPAddressValue,
    OptionalServerReferenceValue,
    PortValue,
    RequiredNameValue,
    ServerReferenceValue,
    ServerTitleValue,
    SshUserValue,
)
from meridian.core.models import CoreModel
from meridian.core.workflow import InputField, InputSection, WorkflowPlan

ServerAuthState = Literal["unknown", "validated", "key_ready", "failed"]
ServerSource = Literal["manual", "legacy", "engine", "imported"]
ServerKeyPolicy = Literal["generate_meridian", "use_existing"]


class ServerConnectionDraft(CoreModel):
    """Unsaved server connection details collected by an onboarding UI."""

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


class ServerValidateRequest(CoreModel):
    """Request to validate either a saved server reference or unsaved draft details."""

    model_config = ConfigDict(
        json_schema_extra={
            "oneOf": [
                {
                    "required": ["server_ref"],
                    "properties": {"server_ref": {"minLength": 1}, "draft": {"type": "null"}},
                },
                {
                    "required": ["draft"],
                    "properties": {"server_ref": {"const": ""}},
                },
            ]
        }
    )

    server_ref: OptionalServerReferenceValue = ""
    draft: ServerConnectionDraft | None = None

    @model_validator(mode="after")
    def require_one_target(self) -> Self:
        if bool(self.server_ref) == bool(self.draft):
            raise ValueError("Choose either a saved server or new server details.")
        return self


class ServerValidateResult(CoreModel):
    """Result from validating SSH reachability and operator permissions."""

    server: ServerProfile
    reachable: bool
    auth_ok: bool
    sudo_ok: bool = False
    detected_os: str = ""
    hints: list[str] = Field(default_factory=list)


class ServerBootstrapKeyRequest(CoreModel):
    """Request metadata for installing or verifying key-based SSH access.

    Passwords are intentionally excluded from this public contract. Executable
    Engine mode must pass one-time password secrets through a separate secret
    channel and never write them into generated schemas, logs, or event data.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                {
                    "if": {"properties": {"key_policy": {"const": "use_existing"}}, "required": ["key_policy"]},
                    "then": {"required": ["public_key"], "properties": {"public_key": {"minLength": 1}}},
                }
            ]
        }
    )

    server_ref: ServerReferenceValue
    key_policy: ServerKeyPolicy = "generate_meridian"
    public_key: str = ""
    disable_password_auth: bool = False

    @model_validator(mode="after")
    def require_public_key_when_reusing_key(self) -> Self:
        if self.key_policy == "use_existing" and not self.public_key.strip():
            raise ValueError("Paste a public key when reusing an existing SSH key.")
        return self


class ServerBootstrapKeyResult(CoreModel):
    """Result from key bootstrap without exposing private key or password material."""

    server: ServerProfile
    key_policy: ServerKeyPolicy
    key_path: str = ""
    installed: bool
    verified: bool
    password_auth_disabled: bool = False
    warnings: list[str] = Field(default_factory=list)


def server_profile_id(host: str, ssh_user: str = "root", ssh_port: int = 22) -> str:
    """Build a stable local ID for a server profile from connection identity."""
    digest = hashlib.sha1(f"{host}|{ssh_user}|{ssh_port}".encode("utf-8")).hexdigest()[:12]
    return f"srv-{digest}"


def profile_from_draft(
    draft: ServerConnectionDraft,
    *,
    auth_state: ServerAuthState = "unknown",
    source: ServerSource = "manual",
) -> ServerProfile:
    """Convert UX-shaped draft input into a saved profile contract."""
    return ServerProfile(
        id=server_profile_id(draft.host, draft.ssh_user, draft.ssh_port),
        title=draft.title,
        host=draft.host,
        ssh_user=draft.ssh_user,
        ssh_port=draft.ssh_port,
        auth_state=auth_state,
        source=source,
    )


def build_server_onboarding_workflow() -> WorkflowPlan:
    """Describe the beginner server setup flow for Studio and future Engine UI."""
    fields = [
        InputField(
            id="title",
            label="Server title",
            kind="text",
            required=True,
            default="My VPS",
            help_text="A name you will recognize later, such as Family VPN or London relay.",
        ),
        InputField(
            id="host",
            label="Server IP address",
            kind="text",
            required=True,
            default="",
            help_text="Use the public IP from your VPS provider.",
        ),
        InputField(
            id="ssh_user",
            label="SSH user",
            kind="text",
            required=True,
            default="root",
            help_text="Use the username from your provider. Non-root users need passwordless sudo.",
        ),
        InputField(
            id="ssh_port",
            label="SSH port",
            kind="text",
            required=True,
            default="22",
            help_text="Use 22 unless your VPS provider gave you a custom SSH port.",
        ),
    ]
    return WorkflowPlan(
        id="server-onboarding",
        title="Add a server",
        summary="Name one server, validate SSH details, and prepare it for key-based access.",
        needs_input=True,
        fields=fields,
        sections=[
            InputSection(
                id="connection",
                title="Connection",
                description="The minimum details needed before Meridian can validate SSH.",
                field_ids=["title", "host", "ssh_user", "ssh_port"],
            )
        ],
        ready_request_schema="server-connection-draft",
    )
