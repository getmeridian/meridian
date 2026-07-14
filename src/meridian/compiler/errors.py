"""Typed failures raised while compiling V4 topology intent."""


class TopologyCompileError(ValueError):
    """User intent cannot be represented by Meridian's finite V4 resources."""
