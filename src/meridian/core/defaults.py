"""Core defaults that must not depend on CLI-layer ``meridian.config``."""

from __future__ import annotations

# Default Reality camouflage target.  Duplicated from ``meridian.config``
# so that the core contract layer stays free of CLI-specific state such as
# paths, image tags, and environment-variable readers.
DEFAULT_SNI: str = "www.google.com"

# Reality camouflage targets tried in order when the operator gives no --sni.
# Each must carry Reality traffic end to end; www.microsoft.com is absent
# because Reality handshakes through it failed in the system lab while these
# succeeded with identical keys and config.
REALITY_SNI_CANDIDATES: tuple[str, ...] = ("www.google.com", "dl.google.com", "www.cloudflare.com")
