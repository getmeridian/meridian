"""Host readiness for Remnawave node plugins (Torrent Blocker, ingress and egress filters).

The plugins are configured in the panel, which Meridian leaves to the operator. They
drive nftables from the host-network node container (``NET_ADMIN``) through the
container's own library, so the host needs no ``nft`` binary, only a 5.7+ kernel;
on an older one they silently do nothing. Plugins are optional, so the fix says so.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from meridian.core.verification import VerificationCheck, VerificationFinding
from meridian.diagnostics.evidence import finding, finish_check
from meridian.facts import ServerFacts

if TYPE_CHECKING:
    from meridian.core.execution import ServerConnection

NODE_PLUGIN_MIN_KERNEL = (5, 7)
_PLUGINS = "Remnawave node plugins (Torrent Blocker, ingress and egress filters)"


def check_node_plugin_host(conn: ServerConnection) -> VerificationCheck:
    """Report whether this node host meets upstream's node-plugin requirements."""
    started = time.monotonic()
    facts = ServerFacts(conn)
    return finish_check("node_plugins", "Node plugin readiness", [_kernel(facts)], started)


def _kernel(facts: ServerFacts) -> VerificationFinding:
    kernel = facts.kernel_release()
    if kernel is None:
        return finding("NODE_PLUGIN_KERNEL", "skipped", "Could not read the kernel release")
    version = kernel.version
    if version is None:
        return finding("NODE_PLUGIN_KERNEL", "skipped", f"Unrecognized kernel release {kernel.release!r}")
    minimum = ".".join(map(str, NODE_PLUGIN_MIN_KERNEL))
    if version < NODE_PLUGIN_MIN_KERNEL:
        return finding(
            "NODE_PLUGIN_KERNEL",
            "warning",
            f"Kernel {kernel.release} is older than {minimum}: {_PLUGINS} do nothing here",
            f"Only if you use node plugins: move to a {minimum}+ kernel (Ubuntu 22.04+ or Debian 11+), then reboot.",
        )
    return finding("NODE_PLUGIN_KERNEL", "passed", f"Kernel {kernel.release} supports node plugins")
