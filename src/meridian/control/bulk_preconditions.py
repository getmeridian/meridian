"""Private, durable undo preconditions over account versions and reviewed access resources."""

from meridian.core.agent_types import Sha256Hex
from meridian.core.models import CoreModel


class BulkPreconditions(CoreModel):
    people: dict[str, tuple[str, int]]
    nodes: Sha256Hex
