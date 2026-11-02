"""Private person routing choices and stateless service reviews."""

from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from meridian.core.agent_types import Sha256Hex
from meridian.core.delivery_policies import DeliveryPolicy, ResolvedDeliveryPolicy
from meridian.core.inputs import TopologyIdValue
from meridian.core.models import CoreModel


class DeliveryPolicyCatalog(CoreModel):
    policies: list[DeliveryPolicy] = Field(max_length=66)
    default_policy: TopologyIdValue
    profile_policies: dict[TopologyIdValue, TopologyIdValue] = Field(max_length=64)


class PersonDeliveryPolicyState(CoreModel):
    username: str = Field(min_length=1, max_length=64)
    override: TopologyIdValue | None
    effective: ResolvedDeliveryPolicy


class PersonDeliveryPolicyWrite(CoreModel):
    """Explicit null inherits; confirm needs the hash returned by the no-write preview."""

    policy: TopologyIdValue | None
    confirm: bool = False
    review_hash: Sha256Hex | None = None

    @model_validator(mode="after")
    def reviewed(self) -> Self:
        if self.confirm and self.review_hash is None:
            raise ValueError("Preview the delivery policy first and send its review_hash when confirming.")
        return self


class PersonDeliveryPolicyReview(CoreModel):
    before: PersonDeliveryPolicyState
    after: PersonDeliveryPolicyState
    review_hash: Sha256Hex
    confirmed: bool = False


class ClientRoutingResult(CoreModel):
    state: PersonDeliveryPolicyState
    review: PersonDeliveryPolicyReview | None = None
    dry_run: bool = False
    applied: bool = False
