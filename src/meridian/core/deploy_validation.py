"""Deploy request validation helpers owned by meridian-core."""

from __future__ import annotations

from meridian.core.deploy import DeployRequest
from meridian.core.inputs import (
    is_ip_deploy_target,
    is_local_deploy_target,
    validate_name_value,
)


class DeployValidationError(ValueError):
    """Raised when a deploy request is not executable."""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


def normalize_client_name(client_name: str) -> str:
    """Validate and default the first deploy client name."""
    if not client_name:
        return "default"
    try:
        return validate_name_value(client_name)
    except ValueError as exc:
        raise DeployValidationError(
            f"Client name '{client_name}' is invalid",
            hint=str(exc),
        ) from exc


def validate_deploy_target(target: str) -> None:
    """Validate a concrete deploy target after registry resolution."""
    if not target:
        raise DeployValidationError(
            "Deploy target is required",
            hint="Enter a server IP address or use --server with a registered server.",
        )
    if not is_local_deploy_target(target) and not is_ip_deploy_target(target):
        raise DeployValidationError(
            f"Invalid IP address: {target}",
            hint="Enter a valid IP address (e.g. meridian deploy 198.51.100.10).",
        )


def normalize_deploy_request(request: DeployRequest) -> DeployRequest:
    """Validate request-level deploy invariants and return a normalized copy."""
    if request.ip and request.requested_server:
        raise DeployValidationError(
            "Use either the IP address or --server, not both.",
            hint="Example: meridian deploy 198.51.100.10 OR meridian deploy --server mybox",
        )
    if not request.ip and not request.requested_server:
        raise DeployValidationError(
            "Deploy target is required",
            hint="Enter a server IP address or run the interactive wizard.",
        )
    return request.model_copy(update={"client_name": normalize_client_name(request.client_name)})
