"""Deploy request/result contracts for meridian-core clients."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from meridian.config import DEFAULT_SNI
from meridian.core.inputs import DeployIpValue, NameValue, OptionalServerReferenceValue, SshUserValue
from meridian.core.models import CoreModel
from meridian.core.serde import to_plain
from meridian.core.workflow import InputField, InputOption, InputSection, WorkflowPlan

DeployMode = Literal["first_deploy", "redeploy"]


class DeployRequest(CoreModel):
    """Trusted local request for deploying or redeploying a Meridian server."""

    ip: DeployIpValue = ""
    domain: str = ""
    sni: str = ""
    client_name: NameValue = ""
    user: SshUserValue = "root"
    yes: bool = False
    harden: bool = True
    requested_server: OptionalServerReferenceValue = ""
    server_name: str = ""
    icon: str = ""
    color: str = ""
    decoy: str = ""
    pq: bool = False
    warp: bool = False
    geo_block: bool = True
    ssh_port: int = Field(default=22, ge=1, le=65535)


class DeployWorkflowAnswers(CoreModel):
    """Answers collected by a deploy wizard renderer."""

    ip: DeployIpValue = ""
    user: SshUserValue = "root"
    sni: str = ""
    domain: str = ""
    harden: bool = True
    client_name: NameValue = ""
    server_name: str = ""
    icon: str = ""
    color: str = ""
    pq: bool = False
    warp: bool = False
    geo_block: bool = True
    confirm: bool = False


class DeployResult(CoreModel):
    """Result returned after a deploy/redeploy operation completes."""

    mode: DeployMode
    server_ip: str
    ssh_user: str
    ssh_port: int
    domain: str
    sni: str
    client_name: str
    harden: bool
    pq: bool
    warp: bool
    geo_block: bool
    panel_url: str
    panel_secret_path: str
    connection_page_path: str
    connection_page_url: str = ""
    subscription_url: str = ""
    test_command: str = ""
    node_count: int
    relay_count: int
    summary: str

    def to_data(self) -> dict[str, Any]:
        return to_plain(self)


def build_deploy_workflow(request: DeployRequest) -> WorkflowPlan:
    """Describe deploy wizard inputs for CLI and future UI clients."""
    needs_input = not request.ip and not request.requested_server
    fields = _deploy_input_fields(request) if needs_input else []
    return WorkflowPlan(
        id="deploy",
        title="Deploy Meridian server",
        summary="Collect deploy target, camouflage, branding, and first-client settings.",
        needs_input=needs_input,
        fields=fields,
        sections=_deploy_input_sections(fields) if needs_input else [],
        ready_request_schema="deploy-request",
    )


def apply_deploy_workflow_answers(request: DeployRequest, answers: DeployWorkflowAnswers) -> DeployRequest:
    """Return a deploy request updated with renderer-collected wizard answers."""
    data = request.model_dump(mode="python")
    data.update(
        {
            "ip": answers.ip,
            "domain": answers.domain,
            "sni": answers.sni,
            "client_name": answers.client_name,
            "user": answers.user,
            "harden": answers.harden,
            "server_name": answers.server_name,
            "icon": answers.icon,
            "color": answers.color,
            "pq": answers.pq,
            "warp": answers.warp,
            "geo_block": answers.geo_block,
            "yes": request.yes or answers.confirm,
        }
    )
    return DeployRequest.model_validate(data)


def _deploy_input_fields(request: DeployRequest) -> list[InputField]:
    return [
        InputField(
            id="ip",
            label="Server IP address",
            kind="text",
            required=True,
            default=request.ip,
            help_text="Use a public server IP, or 'local' when running Meridian on the target server.",
        ),
        InputField(
            id="user",
            label="SSH user",
            kind="text",
            required=True,
            default=request.user or "root",
            help_text="Non-root users must have passwordless sudo.",
        ),
        InputField(
            id="harden",
            label="Harden SSH and firewall",
            kind="boolean",
            default=request.harden,
            help_text="Recommended unless this server already hosts other services.",
        ),
        InputField(
            id="sni",
            label="Camouflage target",
            kind="text",
            required=True,
            default=request.sni or DEFAULT_SNI,
            help_text="A popular website that Reality can impersonate during probes.",
        ),
        InputField(
            id="domain",
            label="Domain",
            kind="text",
            default=request.domain,
            help_text="Optional but strongly recommended for normal HTTPS appearance and CDN fallback.",
        ),
        InputField(
            id="server_name",
            label="Server name",
            kind="text",
            default=request.server_name or "My VPN",
            help_text="Shown on generated connection pages.",
        ),
        InputField(
            id="icon",
            label="Server icon",
            kind="text",
            default=request.icon,
            help_text="Emoji or image URL for the connection page.",
        ),
        InputField(
            id="color",
            label="Color palette",
            kind="choice",
            default=request.color or "ocean",
            options=[
                InputOption(value="ocean", label="Ocean"),
                InputOption(value="sunset", label="Sunset"),
                InputOption(value="forest", label="Forest"),
                InputOption(value="lavender", label="Lavender"),
                InputOption(value="rose", label="Rose"),
                InputOption(value="slate", label="Slate"),
            ],
        ),
        InputField(
            id="client_name",
            label="First client",
            kind="text",
            default=request.client_name or "default",
            help_text="Initial connection profile name.",
        ),
        InputField(
            id="pq",
            label="Post-quantum encryption",
            kind="boolean",
            default=request.pq,
            help_text="Experimental ML-KEM-768 hybrid encryption.",
        ),
        InputField(
            id="warp",
            label="Cloudflare WARP",
            kind="boolean",
            default=request.warp,
            help_text="Route outgoing traffic through Cloudflare WARP.",
        ),
        InputField(
            id="geo_block",
            label="Block RU traffic",
            kind="boolean",
            default=request.geo_block,
            help_text="Blocks RU destinations on this server. Regional exit routing is configured separately.",
        ),
        InputField(
            id="confirm",
            label="Confirm deployment",
            kind="confirmation",
            required=True,
            default=False,
        ),
    ]


def _deploy_input_sections(fields: list[InputField]) -> list[InputSection]:
    field_ids = {field.id for field in fields}

    def keep(ids: list[str]) -> list[str]:
        return [field_id for field_id in ids if field_id in field_ids]

    return [
        InputSection(
            id="target",
            title="Deploy target",
            description="Where Meridian will install the panel and first node.",
            field_ids=keep(["ip", "user", "harden"]),
        ),
        InputSection(
            id="stealth",
            title="Stealth profile",
            description="Reality camouflage and optional domain mode.",
            field_ids=keep(["sni", "domain"]),
        ),
        InputSection(
            id="experience",
            title="Connection page",
            description="Branding and first client profile.",
            field_ids=keep(["server_name", "icon", "color", "client_name"]),
        ),
        InputSection(
            id="advanced",
            title="Advanced options",
            field_ids=keep(["pq", "warp", "geo_block", "confirm"]),
        ),
    ]
