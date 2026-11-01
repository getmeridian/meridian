"""Redacted credential-rotation results for the CLI."""

from typing import Literal

from pydantic import Field

from meridian.control.command_models import NodeCommandOutcome
from meridian.core.client_sessions import CredentialGeneration, UserFlowBlocks
from meridian.core.clients import ClientStatus
from meridian.core.models import CoreModel


class RotatedClient(ClientStatus):
    id: str
    credential_generation: CredentialGeneration


class ClientCredentialRotationResult(CoreModel):
    client: RotatedClient
    subscription_preserved: Literal[True] = True
    flow_blocks: UserFlowBlocks
    cut_off: list[NodeCommandOutcome] | None = Field(default=None)
