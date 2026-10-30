"""Schema registry of the `meridian.agent/v1` service-to-agent protocol.

Kept apart from the public catalog in `core.schema`: the protocol is internal to
the control service and the Rust node agent, so it is exported to
`contracts/meridian/v1/agent/` and never listed by `meridian api schemas`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from meridian.core.agent_desired import AgentResourceSpec, DesiredNodeState
from meridian.core.agent_http import AgentHttpSurface
from meridian.core.agent_protocol import (
    AgentBootstrap,
    AgentCommandSpec,
    AgentErrorResponse,
    AgentMessageSpec,
    CommandResult,
    CommandResultAck,
    EnrollRequest,
    EnrollResponse,
    HelloRequest,
    HelloResponse,
    ObservedStateReport,
    PollRequest,
    PollResponse,
    RenewRequest,
    RenewResponse,
    ReportAck,
    StatsAck,
    StatsReport,
)

AGENT_SCHEMAS: dict[str, type[BaseModel]] = {
    "agent-bootstrap": AgentBootstrap,
    "agent-command": AgentCommandSpec,
    "agent-command-result": CommandResult,
    "agent-command-result-ack": CommandResultAck,
    "agent-desired-state": DesiredNodeState,
    "agent-enroll-request": EnrollRequest,
    "agent-enroll-response": EnrollResponse,
    "agent-error-response": AgentErrorResponse,
    "agent-hello-request": HelloRequest,
    "agent-hello-response": HelloResponse,
    "agent-http-surface": AgentHttpSurface,
    "agent-message": AgentMessageSpec,
    "agent-observed-state-report": ObservedStateReport,
    "agent-poll-request": PollRequest,
    "agent-poll-response": PollResponse,
    "agent-renew-request": RenewRequest,
    "agent-renew-response": RenewResponse,
    "agent-report-ack": ReportAck,
    "agent-resource": AgentResourceSpec,
    "agent-stats-ack": StatsAck,
    "agent-stats-report": StatsReport,
}


def agent_schema(name: str) -> dict[str, Any]:
    """Return the JSON Schema of one agent protocol model, as exported."""
    return AGENT_SCHEMAS[name].model_json_schema(mode="serialization", by_alias=True)
