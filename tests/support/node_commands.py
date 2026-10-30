"""Typed command records for deployer waiting and CLI outcome tests."""

from __future__ import annotations

import base64

from meridian.control.command_models import NodeCommandRecord
from meridian.control.models import NodeSpec
from meridian.core.agent_protocol import CommandResult, ResetInboundCommand, ResourceError, RestartUnitCommand
from meridian.core.agent_types import ServiceSignature


def command_record(
    node_id: str, status: str = "pending", *, index: int = 1, fallback: bool = False, unit: str | None = None
) -> NodeCommandRecord:
    common = {
        "command_id": f"00000000-0000-4000-8000-{index:012d}",
        "fleet_epoch": 1,
        "issued_at": "2026-10-30T12:00:00Z",
        "expires_at": "2026-10-30T12:05:00Z",
        "signature": ServiceSignature(key_id="a" * 64, signature=base64.b64encode(bytes(64)).decode()),
    }
    command = (
        RestartUnitCommand(**common, resource_id=unit)
        if unit
        else ResetInboundCommand(**common, inbound_tag="vless-reality")
    )
    result = None
    if status in {"succeeded", "failed", "rejected"}:
        result = CommandResult(
            command_id=command.command_id,
            status=status,
            error=ResourceError(code="service_failed", message="The node could not restart.", retryable=False)
            if status != "succeeded"
            else None,
            finished_at="2026-10-30T12:00:01Z",
            effect=("core_restart" if fallback else "inbound_reset") if status == "succeeded" and not unit else None,
        )
    return NodeCommandRecord(node_id=node_id, command=command, status=status, wait_for=None, result=result)


TAG = "vless-reality"


def command_node_spec() -> NodeSpec:
    return NodeSpec.model_validate(
        {
            "resources": [
                {
                    "id": "xray:config",
                    "kind": "xray_config",
                    "path": "/etc/netagent/core.json",
                    "config": {"inbounds": [{"tag": TAG, "protocol": "vless", "settings": {"clients": []}}]},
                    "api_address": "127.0.0.1:10085",
                },
                {
                    "id": "unit:netcore",
                    "kind": "systemd_unit",
                    "name": "netcore.service",
                    "depends_on": ["xray:config"],
                    "content": "[Service]\nExecStart=/usr/local/lib/netagent/netcore run\n",
                    "enabled": True,
                    "state": "started",
                    "restart_on": ["xray:config"],
                },
            ],
            "managed_inbounds": [{"tag": TAG, "protocol": "vless"}],
        }
    )
