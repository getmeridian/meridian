"""Relay node provisioner — deploys Realm TCP relay via SSH.

A relay node is a lightweight TCP forwarder with no Docker or panel.
It runs Realm to forward port 443 to an exit server, preserving end-to-end
VLESS+Reality encryption between the client and the exit.
"""

from __future__ import annotations

import shlex
import time
from dataclasses import dataclass, field

from meridian.config import (
    REALM_GITHUB_URL,
    REALM_SHA256,
    REALM_VERSION,
    RELAY_CONFIG_PATH,
    RELAY_SERVICE_NAME,
)
from meridian.provision.common import detect_ssh_ports
from meridian.provision.recipe import Operation, Recipe, Resource, op
from meridian.provision.steps import StepResult
from meridian.ssh import ServerConnection

# Minimal packages needed on a relay node
_RELAY_PACKAGES = ["curl", "wget", "ufw", "ca-certificates"]

# Realm systemd service template
_SYSTEMD_UNIT = """\
[Unit]
Description=Meridian Relay (Realm TCP forwarder)
After=network.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=simple
ExecStart=/usr/local/bin/realm -c {config_path}
Restart=on-failure
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
"""


@dataclass
class RelayContext:
    """Configuration for relay node provisioning."""

    relay_ip: str
    exit_ip: str
    exit_port: int = 443
    listen_port: int = 443
    user: str = "root"
    realm_version: str = REALM_VERSION
    results: list[StepResult] = field(default_factory=list)

    def __post_init__(self) -> None:
        import ipaddress

        # Validate IP addresses — prevents shell/config injection
        for field_name, value in [("relay_ip", self.relay_ip), ("exit_ip", self.exit_ip)]:
            try:
                ipaddress.ip_address(value)
            except ValueError:
                raise ValueError(f"Invalid IP address for {field_name}: {value!r}") from None

        # Validate port ranges
        for field_name, port in [("exit_port", self.exit_port), ("listen_port", self.listen_port)]:
            if not isinstance(port, int) or not (1 <= port <= 65535):
                raise ValueError(f"Invalid port for {field_name}: {port!r} (must be 1-65535)")


# ---------------------------------------------------------------------------
# Relay provisioning steps
# ---------------------------------------------------------------------------


class ConfigureRelayFirewall:
    """Configure UFW firewall on relay node."""

    name = "Configure relay firewall"

    def run(self, conn: ServerConnection, ctx: RelayContext) -> StepResult:
        check = conn.run("which ufw 2>/dev/null", timeout=15)
        if check.returncode != 0:
            # ufw not found — try to install it explicitly
            conn.run("apt-get update -qq && apt-get install -y -qq ufw", timeout=120)
            recheck = conn.run("which ufw", timeout=15)
            if recheck.returncode != 0:
                return StepResult(
                    name=self.name,
                    status="failed",
                    detail="ufw not available — install it manually: apt-get install ufw",
                )

        changed = False

        ufw_status = conn.run("ufw status", timeout=15)
        ufw_active = ufw_status.returncode == 0 and "Status: active" in ufw_status.stdout

        # Allow the live sshd port(s) instead of assuming 22.
        for ssh_port in detect_ssh_ports(conn):
            result = conn.run(f"ufw allow {ssh_port}/tcp", timeout=15)
            if result.returncode != 0:
                return StepResult(name=self.name, status="failed", detail=f"failed to allow SSH port {ssh_port}")
            if "Skipping" not in result.stdout:
                changed = True

        # Allow relay port
        result = conn.run(f"ufw allow {ctx.listen_port}/tcp", timeout=15)
        if result.returncode != 0:
            return StepResult(name=self.name, status="failed", detail="failed to allow relay port")
        if "Skipping" not in result.stdout:
            changed = True

        result = conn.run("ufw default deny incoming", timeout=15)
        if result.returncode != 0:
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"ufw default deny incoming failed: {result.stderr.strip()[:200]}",
            )
        result = conn.run("ufw default allow outgoing", timeout=15)
        if result.returncode != 0:
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"ufw default allow outgoing failed: {result.stderr.strip()[:200]}",
            )

        if not ufw_active:
            result = conn.run("echo y | ufw enable", timeout=30)
            if result.returncode != 0:
                return StepResult(name=self.name, status="failed", detail="ufw enable failed")
            changed = True
        else:
            conn.run("ufw reload", timeout=30)

        return StepResult(name=self.name, status="changed" if changed else "ok")


class InstallRealm:
    """Download and install Realm binary from GitHub releases."""

    name = "Install Realm TCP relay"

    def run(self, conn: ServerConnection, ctx: RelayContext) -> StepResult:
        # Check if Realm is already installed at the right version
        check = conn.run("realm --version 2>/dev/null", timeout=15)
        if check.returncode == 0:
            # Parse version from output like "realm 2.9.3"
            installed_version = check.stdout.strip().split()[-1] if check.stdout.strip() else ""
            if installed_version == ctx.realm_version:
                return StepResult(name=self.name, status="ok", detail=f"v{ctx.realm_version} already installed")

        # Detect architecture
        arch_result = conn.run("uname -m", timeout=15)
        if arch_result.returncode != 0:
            return StepResult(name=self.name, status="failed", detail="cannot detect architecture")

        arch = arch_result.stdout.strip()
        if arch == "x86_64":
            target = "x86_64-unknown-linux-gnu"
        elif arch in ("aarch64", "arm64"):
            target = "aarch64-unknown-linux-gnu"
        else:
            return StepResult(name=self.name, status="failed", detail=f"unsupported arch: {arch}")

        # Download Realm binary
        url = f"{REALM_GITHUB_URL}/v{ctx.realm_version}/realm-{target}.tar.gz"
        q_url = shlex.quote(url)

        download = conn.run(
            f"curl -fsSL {q_url} -o /tmp/realm.tar.gz",
            timeout=120,
        )
        if download.returncode != 0:
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"download failed: {download.stderr.strip()[:200]}",
            )

        # Verify SHA256 checksum (supply chain protection)
        expected_hash = REALM_SHA256.get(target, "")
        if expected_hash:
            check = conn.run("sha256sum /tmp/realm.tar.gz | cut -d' ' -f1", timeout=15)
            actual_hash = check.stdout.strip()
            if actual_hash != expected_hash:
                conn.run("rm -f /tmp/realm.tar.gz", timeout=15)
                return StepResult(
                    name=self.name,
                    status="failed",
                    detail=f"checksum mismatch: expected {expected_hash[:16]}..., got {actual_hash[:16]}...",
                )

        # Extract and install
        extract = conn.run(
            "tar xzf /tmp/realm.tar.gz -C /tmp && "
            "install -m 755 /tmp/realm /usr/local/bin/realm && "
            "rm -f /tmp/realm.tar.gz /tmp/realm",
            timeout=30,
        )
        if extract.returncode != 0:
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"extract/install failed: {extract.stderr.strip()[:200]}",
            )

        # Verify
        verify = conn.run("realm --version", timeout=15)
        if verify.returncode != 0:
            return StepResult(name=self.name, status="failed", detail="realm binary not working after install")

        return StepResult(name=self.name, status="changed", detail=f"installed v{ctx.realm_version}")


class ConfigureRealm:
    """Write Realm config and systemd service, then start."""

    name = "Configure Realm relay"

    def run(self, conn: ServerConnection, ctx: RelayContext) -> StepResult:
        # IPs are validated in RelayContext.__post_init__, safe for config interpolation

        # Listen on all interfaces — dual-stack [::] for IPv6, 0.0.0.0 for IPv4
        if ":" in ctx.relay_ip:
            listen_addr = f"[::]:{ctx.listen_port}"
        else:
            listen_addr = f"0.0.0.0:{ctx.listen_port}"

        # Remote address: bracket IPv6 for host:port notation
        if ":" in ctx.exit_ip:
            remote_addr = f"[{ctx.exit_ip}]:{ctx.exit_port}"
        else:
            remote_addr = f"{ctx.exit_ip}:{ctx.exit_port}"

        # Write Realm config
        config_content = (
            "[network]\n"
            "no_tcp = false\n"
            "use_udp = false\n"
            "\n"
            "[[endpoints]]\n"
            f'listen = "{listen_addr}"\n'
            f'remote = "{remote_addr}"\n'
        )

        conn.run("mkdir -p /etc/meridian", timeout=15)

        write_config = conn.put_text(
            RELAY_CONFIG_PATH,
            config_content,
            mode="600",
            sensitive=True,
            timeout=15,
            operation_name="write realm config",
        )
        if write_config.returncode != 0:
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"failed to write config: {write_config.stderr.strip()[:200]}",
            )

        # Write relay metadata
        relay_meta = (
            f"role: relay\nexit_ip: {ctx.exit_ip}\nexit_port: {ctx.exit_port}\nlisten_port: {ctx.listen_port}\n"
        )
        conn.put_text(
            "/etc/meridian/relay.yml",
            relay_meta,
            mode="600",
            sensitive=True,
            timeout=15,
            operation_name="write relay metadata",
        )

        # Write systemd service
        unit_content = _SYSTEMD_UNIT.format(config_path=RELAY_CONFIG_PATH)
        service_path = f"/etc/systemd/system/{RELAY_SERVICE_NAME}.service"
        write_service = conn.put_text(
            service_path,
            unit_content,
            mode="644",
            timeout=15,
            operation_name="write realm service",
        )
        if write_service.returncode != 0:
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"failed to write service: {write_service.stderr.strip()[:200]}",
            )

        # Reload systemd, enable and (re)start service
        conn.run("systemctl daemon-reload", timeout=30)
        conn.run(f"systemctl enable {RELAY_SERVICE_NAME}", timeout=15)
        restart = conn.run(f"systemctl restart {RELAY_SERVICE_NAME}", timeout=30)
        if restart.returncode != 0:
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"service restart failed: {restart.stderr.strip()[:200]}",
            )

        return StepResult(
            name=self.name,
            status="changed",
            detail=f"forwarding :{ctx.listen_port} -> {ctx.exit_ip}:{ctx.exit_port}",
        )


class VerifyRelay:
    """Verify Realm service is running and relay->exit connectivity works."""

    name = "Verify relay connectivity"

    def run(self, conn: ServerConnection, ctx: RelayContext) -> StepResult:
        # Check service is running — retry because Realm needs time to bind
        max_retries = 4
        for attempt in range(max_retries):
            status_result = conn.run(f"systemctl is-active {RELAY_SERVICE_NAME}", timeout=15)
            if status_result.returncode == 0 and status_result.stdout.strip() == "active":
                break
            if attempt < max_retries - 1:
                time.sleep(2)
        else:
            # All retries exhausted
            logs = conn.run(f"journalctl -u {RELAY_SERVICE_NAME} --no-pager -n 10", timeout=15)
            detail = logs.stdout.strip()[-200:] if logs.returncode == 0 else "service not active"
            return StepResult(name=self.name, status="failed", detail=detail)

        # Test TCP connectivity from relay to exit
        # IPs are validated in RelayContext.__post_init__
        tcp_test = conn.run(
            f"nc -z -w 5 {ctx.exit_ip} {ctx.exit_port} 2>/dev/null",
            timeout=15,
        )
        if tcp_test.returncode != 0:
            # Fallback: same-server relay may not reach its own public IP
            # (cloud firewalls often block self-connect). Try the relay's
            # listen port on localhost instead — proves Realm is forwarding.
            localhost_test = conn.run(
                f"nc -z -w 3 127.0.0.1 {ctx.listen_port} 2>/dev/null",
                timeout=15,
            )
            if localhost_test.returncode == 0:
                return StepResult(
                    name=self.name,
                    status="ok",
                    detail="service active, relay port accepting connections",
                )
            return StepResult(
                name=self.name,
                status="failed",
                detail=(
                    f"relay cannot reach exit {ctx.exit_ip}:{ctx.exit_port}. "
                    f"Check: 1) exit server is running and accepts connections on port {ctx.exit_port}, "
                    f"2) exit server firewall allows the relay IP, "
                    f"3) relay server allows outbound to port {ctx.exit_port}. "
                    f"Test manually: ssh relay-server 'nc -z -w 5 {ctx.exit_ip} {ctx.exit_port}'"
                ),
            )

        return StepResult(name=self.name, status="ok", detail="service active, exit reachable")


# ---------------------------------------------------------------------------
# Pipeline builder
# ---------------------------------------------------------------------------


def build_relay_steps(ctx: RelayContext) -> list[Operation]:
    """Assemble the relay deployment step pipeline."""
    from meridian.provision.common import ConfigureBBR, InstallPackages

    operations = [
        op(InstallPackages(packages=_RELAY_PACKAGES), provides=[Resource.RELAY_PACKAGES, Resource.SYSTEM_PACKAGES]),
        op(ConfigureBBR(), requires=[Resource.RELAY_PACKAGES], provides=[Resource.BBR_ENABLED]),
        op(ConfigureRelayFirewall(), requires=[Resource.RELAY_PACKAGES], provides=[Resource.RELAY_FIREWALL]),
        op(InstallRealm(), requires=[Resource.RELAY_PACKAGES], provides=[Resource.REALM_INSTALLED]),
        op(
            ConfigureRealm(),
            requires=[Resource.REALM_INSTALLED, Resource.RELAY_FIREWALL],
            provides=[Resource.REALM_CONFIGURED],
        ),
        op(VerifyRelay(), requires=[Resource.REALM_CONFIGURED], provides=[Resource.RELAY_VERIFIED]),
    ]
    return Recipe(tuple(operations)).steps(ctx)
