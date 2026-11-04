"""Cloudflare WARP provisioning steps.

WARP routes server egress through Cloudflare's network in proxy mode
(SOCKS5 on 127.0.0.1:40000). Xray uses this as its default outbound,
so destination sites see a Cloudflare IP instead of the VPS IP.

Only outgoing proxy traffic is affected — incoming connections (SSH,
nginx, Xray inbound) are completely unaffected.
"""

from __future__ import annotations

from meridian.facts import ServerFacts
from meridian.provision.apt_keys import verify_apt_key
from meridian.provision.ensure import apt_failure, apt_update_and_install, run_apt
from meridian.provision.steps import ProvisionContext, StepResult
from meridian.ssh import ServerConnection

WARP_PROXY_PORT = 40000
WARP_SOURCE_LIST = "/etc/apt/sources.list.d/cloudflare-client.list"
WARP_KEYRING = "/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg"


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
            key_download = "/tmp/meridian-cloudflare-warp.asc"
            result = conn.run(f"curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg -o {key_download}", timeout=30)
            if result.returncode != 0:
                return StepResult(name=self.name, status="failed", detail="Failed to download the WARP signing key")
            key_error = verify_apt_key(conn, key_download, "cloudflare-warp")
            if key_error:
                return StepResult(name=self.name, status="failed", detail=key_error)
            result = conn.run(
                f"gpg --yes --dearmor --output {WARP_KEYRING} {key_download} && rm -f {key_download}",
                timeout=30,
            )
            if result.returncode != 0:
                return StepResult(name=self.name, status="failed", detail="Failed to add GPG key")

            # Minimal Debian images ship without lsb_release; os-release always exists.
            facts = ServerFacts(conn)
            result = conn.put_text(
                WARP_SOURCE_LIST,
                f"deb [arch={facts.dpkg_arch()}"
                f" signed-by={WARP_KEYRING}]"
                f" https://pkg.cloudflareclient.com/ {facts.os_release().version_codename} main\n",
                mode="644",
                timeout=15,
            )
            if result.returncode != 0:
                return StepResult(name=self.name, status="failed", detail="Failed to add APT repository")

            result = apt_update_and_install(conn, "-y -qq cloudflare-warp", timeout=120)
            if result.returncode != 0:
                detail = f"Failed to install cloudflare-warp: {apt_failure(result)}"
                return StepResult(name=self.name, status="failed", detail=detail)

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


class RemoveWarp:
    """Remove the WARP client an exit no longer routes through: its Cloudflare registration, daemon,
    package, and the APT source this module added."""

    name = "Remove Cloudflare WARP"

    def run(self, conn: ServerConnection, ctx: ProvisionContext) -> StepResult:
        # A stopped daemon refuses warp-cli, so the registration goes first; both may already be gone.
        stop = conn.run(
            "warp-cli --accept-tos disconnect >/dev/null 2>&1;"
            " warp-cli --accept-tos registration delete >/dev/null 2>&1;"
            " systemctl disable --now warp-svc >/dev/null 2>&1; true",
            timeout=60,
        )
        result = run_apt(conn, "apt-get purge -y -qq cloudflare-warp", timeout=120, operation_name="WARP removal")
        if result.returncode != 0:
            detail = f"Failed to remove cloudflare-warp: {apt_failure(result)}"
            return StepResult(name=self.name, status="failed", detail=detail, commands=[stop, result])
        cleanup = conn.run(f"rm -f {WARP_SOURCE_LIST} {WARP_KEYRING}", timeout=15)
        if cleanup.returncode != 0:
            detail = f"Failed to remove the WARP APT source: {cleanup.stderr.strip() or cleanup.returncode}"
            return StepResult(name=self.name, status="failed", detail=detail, commands=[stop, result, cleanup])
        return StepResult(name=self.name, status="changed", detail="WARP removed", commands=[stop, result, cleanup])
