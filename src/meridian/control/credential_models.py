"""Connection-key rotation preserves subscriptions and reports its node effects."""

from typing import Literal

from pydantic import Field

from meridian.control.command_models import NodeCommandRecord
from meridian.control.models import UserRecord
from meridian.core.client_sessions import UserFlowBlocks
from meridian.core.models import CoreModel


class UserCredentialRotation(CoreModel):
    user: UserRecord
    subscription_preserved: Literal[True] = True
    flow_blocks: UserFlowBlocks
    commands: list[NodeCommandRecord] = Field(default_factory=list)
