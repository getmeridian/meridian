"""Core defaults that must not depend on CLI-layer ``meridian.config``."""

from __future__ import annotations

# Default Reality camouflage target.  Duplicated from ``meridian.config``
# so that the core contract layer stays free of CLI-specific state such as
# paths, image tags, and environment-variable readers.
DEFAULT_SNI: str = "www.microsoft.com"
