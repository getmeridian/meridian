"""Cloudflare WARP provisioning steps.

WARP routes server egress through Cloudflare's network in proxy mode
(SOCKS5 on 127.0.0.1:40000). Xray uses this as its default outbound,
so destination sites see a Cloudflare IP instead of the VPS IP.

Only outgoing proxy traffic is affected — incoming connections (SSH,
nginx, Xray inbound) are completely unaffected.
"""

from __future__ import annotations

from meridian.provision.steps import ProvisionContext, StepResult
from meridian.ssh import ServerConnection

WARP_PROXY_PORT = 40000


# ---------------------------------------------------------------------------
# InstallWarp
# ---------------------------------------------------------------------------


class InstallWarp:
    """Install Cloudflare WARP client in proxy mode (SOCKS5 on localhost)."""

    name = "Install Cloudflare WARP"

    def run(self, conn: ServerConnection, ctx: ProvisionContext) -> StepResult:
        # Check if already installed and connected
        status = conn.run("warp-cli --accept-tos status 2>/dev/null", timeout=10)
        if status.returncode == 0 and "Connected" in status.stdout:
            return StepResult(name=self.name, status="ok", detail="WARP already connected")

        installed = conn.run("command -v warp-cli 2>/dev/null", timeout=10)
        if installed.returncode != 0:
            # Add Cloudflare APT repository
            result = conn.run(
                "curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg"
                " | gpg --yes --dearmor --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg",
                timeout=30,
            )
            if result.returncode != 0:
                return StepResult(name=self.name, status="failed", detail="Failed to add GPG key")

            result = conn.run(
                'echo "deb [arch=amd64 signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg]'
                ' https://pkg.cloudflareclient.com/ $(lsb_release -cs) main"'
                " | tee /etc/apt/sources.list.d/cloudflare-client.list",
                timeout=15,
            )
            if result.returncode != 0:
                return StepResult(name=self.name, status="failed", detail="Failed to add APT repository")

            result = conn.run("apt-get update -qq && apt-get install -y -qq cloudflare-warp", timeout=120)
            if result.returncode != 0:
                return StepResult(name=self.name, status="failed", detail="Failed to install cloudflare-warp")

        # Ensure service is running
        svc = conn.run("systemctl enable --now warp-svc", timeout=30)
        if svc.returncode != 0:
            return StepResult(name=self.name, status="failed", detail=f"Failed to start warp-svc: {svc.stderr.strip()}")

        # Register (--accept-tos required for non-TTY, idempotent)
        reg = conn.run("warp-cli --accept-tos registration new 2>/dev/null", timeout=30)
        if reg.returncode != 0:
            # Already registered is fine
            check = conn.run("warp-cli --accept-tos registration show 2>/dev/null", timeout=10)
            if check.returncode != 0:
                return StepResult(name=self.name, status="failed", detail="Failed to register WARP")

        # Set proxy mode (SOCKS5 on localhost — safe for servers).
        # CLI syntax changed between versions:
        #   old: warp-cli set-mode proxy / set-proxy-port PORT
        #   new: warp-cli mode proxy    / proxy port PORT
        mode_result = conn.run("warp-cli --accept-tos mode proxy", timeout=10)
        if mode_result.returncode != 0:
            mode_result = conn.run("warp-cli --accept-tos set-mode proxy", timeout=10)
        if mode_result.returncode != 0:
            return StepResult(name=self.name, status="failed", detail="Failed to set proxy mode")

        port_result = conn.run(f"warp-cli --accept-tos proxy port {WARP_PROXY_PORT}", timeout=10)
        if port_result.returncode != 0:
            fallback_port = conn.run(f"warp-cli --accept-tos set-proxy-port {WARP_PROXY_PORT}", timeout=10)
            if fallback_port.returncode != 0:
                detail = f"Failed to set proxy port: {fallback_port.stderr.strip()}"
                return StepResult(name=self.name, status="failed", detail=detail)

        # Connect
        connect_result = conn.run("warp-cli --accept-tos connect", timeout=30)
        if connect_result.returncode != 0:
            detail = f"Failed to connect WARP: {connect_result.stderr.strip()}"
            return StepResult(name=self.name, status="failed", detail=detail)

        # Wait briefly for connection to establish
        import time

        time.sleep(3)

        # Verify — WARP may fail to connect if the hosting provider or ISP
        # blocks Cloudflare's tunnel endpoints (common in Russia, China).
        # This is not a deploy-breaking issue — return changed with a warning.
        status = conn.run("warp-cli --accept-tos status 2>/dev/null", timeout=10)
        if "Connected" in status.stdout:
            return StepResult(name=self.name, status="changed", detail=f"WARP proxy on 127.0.0.1:{WARP_PROXY_PORT}")

        return StepResult(
            name=self.name,
            status="changed",
            detail="WARNING: installed but not connected — network may block WARP endpoints",
        )


# Note: WARP outbound configuration is handled in _build_xray_config (setup.py)
# which adds the WARP SOCKS5 outbound to the Xray config profile at deploy time.
# The old ConfigureWarpOutbound provisioner step is no longer needed.
