"""Typed failures raised while compiling V4 topology intent."""

from meridian.core.errors import MeridianError


class TopologyCompileError(MeridianError, ValueError):
    """User intent cannot be represented by Meridian's finite V4 resources."""

    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            hint="Revise the V4 topology in `meridian setup`, then review the compiled plan again.",
            category="user",
        )
