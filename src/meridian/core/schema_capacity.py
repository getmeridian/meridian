"""Public server-capacity schema family, separate from agent protocol messages."""

from pydantic import BaseModel

from meridian.core.capacity import CapacityCurrent, CapacityQuery, NodeCapacityView

CAPACITY_SCHEMAS: dict[str, type[BaseModel]] = {
    "node-capacity": NodeCapacityView,
    "node-capacity-current": CapacityCurrent,
    "node-capacity-query": CapacityQuery,
}
