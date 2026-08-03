"""Protocol capability model for Meridian nodes.

Detects what protocol features a running node supports based on its
Xray version. This is a data model + detection layer — enforcement
(e.g., "this config requires XHTTP H3 but the node doesn't support
it") is intentionally deferred to a future change.

No CLI, console, or Rich imports — this is a library module.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class NodeCapabilities:
    """Protocol capabilities detected from a running node."""

    xray_version: str = ""

    # Transport capabilities
    reality: bool = True
    xhttp: bool = True
    xhttp_h2: bool = False  # requires grpc_pass or H2 upstream
    xhttp_h3: bool = False  # requires QUIC listener
    wss: bool = True  # domain mode only

    # Experimental / future protocols
    hysteria2: bool = False  # requires UDP
    finalmask: bool = False  # requires Xray 26.x+

    @classmethod
    def from_xray_version(cls, version: str) -> NodeCapabilities:
        """Derive capabilities from Xray version string.

        Parses a semantic version and enables features based on the
        major version number. Unknown or empty versions get safe
        defaults (Reality + XHTTP + WSS only).
        """
        caps = cls(xray_version=version, reality=True, xhttp=True, wss=True)
        if not version:
            return caps
        try:
            major = int(version.split(".")[0])
            if major >= 26:
                caps.hysteria2 = True
                caps.finalmask = True
        except (ValueError, IndexError):
            pass
        return caps

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict suitable for JSON output."""
        return asdict(self)


# ---- Version parsing helpers ------------------------------------------------

_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")


def _parse_xray_version(output: str) -> str:
    """Extract a semver triple from Xray version output.

    Handles formats like:
      "Xray 26.3.27 (Xray, Penetrates Everything.)"
      "26.3.27"
      "v1.8.24"
    """
    m = _VERSION_RE.search(output)
    return m.group(1) if m else ""


# ---- Detection (requires a live ServerConnection) ---------------------------


def detect_capabilities(conn: Any) -> NodeCapabilities:
    """Detect protocol capabilities from a running Remnawave node.

    ``conn`` must be a ``ServerConnection``-like object with a
    ``run(command, timeout=...)`` method that returns an object with
    ``returncode`` and ``stdout`` attributes. The parameter is typed
    as ``Any`` to avoid importing SSH at module level (this is a
    library module that must stay import-light).
    """
    # Try the Xray binary bundled inside the Remnawave node container
    result = conn.run(
        "docker exec remnawave-node rw-core version 2>/dev/null",
        timeout=15,
    )
    version = ""
    if result.returncode == 0:
        version = _parse_xray_version(result.stdout)

    return NodeCapabilities.from_xray_version(version)
