"""Which CLI, control service, and node agent versions work together (#95).

One rule per pair:

1. CLI and service. Every apply that changes the service installs it at exactly the CLI's
   own version (`meridian-vpn==<cli>`, or the checkout's wheel). A service older than the
   CLI is therefore upgraded by the next apply. A service newer than the CLI is refused
   before anything changes: installing the older package would downgrade it, and its
   database refuses to open under an older schema.
2. Service and agent. The protocol name (`meridian.agent/vN`) is the compatibility unit.
   The service accepts a hello that offers any protocol in `SUPPORTED_AGENT_PROTOCOLS` and
   answers anything else `unsupported_protocol` (not retryable, retry after
   `UNSUPPORTED_PROTOCOL_RETRY_SECONDS`), which the agent logs and backs off on. Within one
   protocol every agent version works with every service version: features ride the
   negotiated capabilities, never version comparisons.
3. CLI and agent. The CLI installs the agent build it ships (compared by SHA-256) on every
   apply, so after an apply each node runs the CLI's agent; `check` warns about a node
   whose reported agent version differs from the one the CLI would install.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from packaging.version import InvalidVersion, Version

from meridian.core.agent_types import AGENT_PROTOCOL

# Oldest first. A protocol leaves this list only in a release that also stops shipping agents for it.
SUPPORTED_AGENT_PROTOCOLS: tuple[str, ...] = (AGENT_PROTOCOL,)
# Long enough not to hammer the service, short enough that a fixed fleet recovers soon.
UNSUPPORTED_PROTOCOL_RETRY_SECONDS = 300

ServiceSkew = Literal["same", "service_older", "service_newer", "unknown"]


def service_skew(cli_version: str, service_version: str) -> ServiceSkew:
    """Compare the control service's version with this CLI's (PEP 440; `unknown` when either is unparsable)."""
    try:
        cli, service = Version(cli_version), Version(service_version)
    except InvalidVersion:
        return "unknown"
    if service == cli:
        return "same"
    return "service_older" if service < cli else "service_newer"


def newer_service_hint(service_version: str) -> str:
    """How to get a CLI that may manage a service at *service_version*."""
    return f"Upgrade this Meridian to {service_version} or later (meridian update), then retry."


def common_agent_protocol(offered: Iterable[object]) -> str | None:
    """The newest protocol both the service and the agent speak, or None."""
    names = {item for item in offered if isinstance(item, str)}
    return next((name for name in reversed(SUPPORTED_AGENT_PROTOCOLS) if name in names), None)


def agent_version_differs(expected: str, reported: str | None) -> bool:
    """Whether a node reports another agent version than the CLI installs; unknown either side is no finding."""
    return bool(expected and reported and expected != reported)
