"""OS-level provisioning steps: packages, hardening, firewall, sysctl."""

from __future__ import annotations

import shlex

from meridian.facts import ServerFacts
from meridian.provision.ensure import ensure_file_content, ensure_packages, ensure_service_running, ensure_ufw_rule
from meridian.provision.steps import ProvisionContext, StepContext, StepResult
from meridian.ssh import ServerConnection

# Minimum disk space required (in MB)
MIN_DISK_SPACE_MB = 2048

# Packages required by the deployment stack
REQUIRED_PACKAGES = [
    "curl",
    "cron",
    "socat",
    "wget",
    "unzip",
    "jq",
    "ufw",
    "gnupg",
    "apt-transport-https",
    "ca-certificates",
    "python3-pip",
    "unattended-upgrades",
    "apt-listchanges",
    "dnsutils",
]

# Auto-upgrades config content
_AUTO_UPGRADES_CONF = """\
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
"""

# BBR sysctl settings
_BBR_SETTINGS = {
    "net.core.default_qdisc": "fq",
    "net.ipv4.tcp_congestion_control": "bbr",
}

_SSH_HARDENING_DROPIN_PATH = "/etc/ssh/sshd_config.d/00-meridian.conf"
_SSH_HARDENING_DROPIN_OLD_PATH = "/etc/ssh/sshd_config.d/99-meridian.conf"
_SSH_HARDENING_DROPIN = """\
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
DebianBanner no
"""


def detect_ssh_ports(conn: ServerConnection) -> list[int]:
    """Detect effective sshd listen ports, falling back to 22."""
    return ServerFacts(conn).ssh_ports()


class CheckDiskSpace:
    """Verify sufficient disk space before deployment."""

    name = "Check disk space"

    def run(self, conn: ServerConnection, ctx: ProvisionContext) -> StepResult:
        avail_mb = ServerFacts(conn).free_disk_mb("/")
        if avail_mb is None:
            return StepResult(name=self.name, status="skipped", detail="could not check disk space")

        if avail_mb < MIN_DISK_SPACE_MB:
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"only {avail_mb}MB free, need {MIN_DISK_SPACE_MB}MB. Run: df -h",
            )

        return StepResult(name=self.name, status="ok", detail=f"{avail_mb}MB available")


class InstallPackages:
    """Install required system packages via apt."""

    name = "Install system packages"

    def __init__(self, packages: list[str] | None = None) -> None:
        self._packages = packages or REQUIRED_PACKAGES

    def run(self, conn: ServerConnection, ctx: StepContext) -> StepResult:
        result = ensure_packages(conn, self._packages)
        if not result.ok:
            stderr = result.detail
            if "no longer has a Release file" in stderr:
                return StepResult(
                    name=self.name,
                    status="failed",
                    detail=(
                        "OS version is end-of-life — package repos have been removed. "
                        "Reinstall with an Ubuntu LTS version"
                    ),
                )
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"apt failed: {stderr[:200]}",
            )

        return StepResult(name=self.name, status="changed" if result.changed else "ok", detail=result.detail)


class EnableAutoUpgrades:
    """Enable automatic security updates via unattended-upgrades."""

    name = "Enable automatic security updates"

    def run(self, conn: ServerConnection, ctx: ProvisionContext) -> StepResult:
        result = ensure_file_content(conn, "/etc/apt/apt.conf.d/20auto-upgrades", _AUTO_UPGRADES_CONF, mode="644")
        if not result.ok:
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"failed to write config: {result.detail}",
            )

        return StepResult(name=self.name, status="changed" if result.changed else "ok", detail=result.detail)


class SetTimezone:
    """Set system timezone to UTC."""

    name = "Set timezone to UTC"

    def run(self, conn: ServerConnection, ctx: ProvisionContext) -> StepResult:
        # Check current timezone
        tz_cmd = "timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null"
        check = conn.run(tz_cmd, timeout=15)
        if check.returncode == 0 and check.stdout.strip() == "UTC":
            return StepResult(name=self.name, status="ok", detail="already UTC")

        result = conn.run("timedatectl set-timezone UTC", timeout=15)
        if result.returncode != 0:
            # Fallback: write directly
            result = conn.run("ln -sf /usr/share/zoneinfo/UTC /etc/localtime", timeout=15)
            if result.returncode != 0:
                return StepResult(
                    name=self.name,
                    status="failed",
                    detail=result.stderr.strip()[:200],
                )

        return StepResult(name=self.name, status="changed")


class HardenSSH:
    """Install an authoritative sshd drop-in and restart sshd."""

    name = "Harden SSH configuration"

    def run(self, conn: ServerConnection, ctx: ProvisionContext) -> StepResult:
        commands: list = []
        changed = False
        ensure = ensure_file_content(
            conn,
            _SSH_HARDENING_DROPIN_PATH,
            _SSH_HARDENING_DROPIN,
            mode="644",
            create_parent=True,
        )
        if not ensure.ok:
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"failed to write sshd hardening drop-in: {ensure.detail}",
                commands=commands,
            )
        if ensure.changed:
            rm_result = conn.run(f"rm -f {_SSH_HARDENING_DROPIN_OLD_PATH}", timeout=15)
            commands.append(rm_result)
            # Neutralize conflicting settings in other drop-ins (e.g., cloud-init)
            # so our values are authoritative regardless of load order.
            neutralize = conn.run(
                "for f in /etc/ssh/sshd_config.d/*.conf; do "
                f'[ "$f" = "{_SSH_HARDENING_DROPIN_PATH}" ] && continue; '
                "sed -i -E "
                r"'s/^[[:space:]]*(PasswordAuthentication"
                r"|KbdInteractiveAuthentication"
                r"|ChallengeResponseAuthentication)"
                r"[[:space:]]/#\1 /' "
                '"$f" 2>/dev/null; done',
                timeout=15,
            )
            commands.append(neutralize)
            changed = True

        # Validate config before restarting
        validate = conn.run("sshd -t", timeout=15)
        commands.append(validate)
        if validate.returncode != 0:
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"sshd config validation failed: {validate.stderr.strip()[:200]}",
                commands=commands,
            )

        # Validate effective settings too — cloud-init drop-ins can override the main file.
        # DebianBanner is a Debian/Ubuntu-specific directive that some OpenSSH
        # builds don't recognize — sshd -T silently omits it.  We still write
        # it (harmless when unsupported), but skip verification when absent.
        _required = ("passwordauthentication no", "kbdinteractiveauthentication no")
        _optional = ("debianbanner no",)

        for setting in _required:
            effective = conn.run(f"sshd -T | grep -q '^{setting}$'", timeout=15)
            commands.append(effective)
            if effective.returncode != 0:
                return StepResult(
                    name=self.name,
                    status="failed",
                    detail=f"effective sshd setting mismatch: expected '{setting}'",
                    commands=commands,
                )

        for setting in _optional:
            effective = conn.run(f"sshd -T | grep -qi '^{setting.split()[0]}'", timeout=15)
            commands.append(effective)
            if effective.returncode != 0:
                # sshd doesn't recognize this directive — skip verification
                continue
            check = conn.run(f"sshd -T | grep -q '^{setting}$'", timeout=15)
            commands.append(check)
            if check.returncode != 0:
                return StepResult(
                    name=self.name,
                    status="failed",
                    detail=f"effective sshd setting mismatch: expected '{setting}'",
                    commands=commands,
                )

        if not changed:
            return StepResult(name=self.name, status="ok", detail="already hardened", commands=commands)

        # Restart sshd (service is named "sshd" on some distros, "ssh" on others)
        restart = conn.run("systemctl restart sshd 2>/dev/null || systemctl restart ssh", timeout=15)
        commands.append(restart)
        if restart.returncode != 0:
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"sshd restart failed: {restart.stderr.strip()[:200]}",
                commands=commands,
            )

        return StepResult(name=self.name, status="changed", commands=commands)


class ConfigureFail2ban:
    """Enable fail2ban with sshd jail for brute-force protection.

    Assumes fail2ban is already installed by InstallPackages.
    """

    name = "Configure fail2ban"

    def run(self, conn: ServerConnection, ctx: StepContext) -> StepResult:
        active = conn.run("systemctl is-active fail2ban", timeout=15)
        if active.returncode == 0 and active.stdout.strip() == "active":
            return StepResult(name=self.name, status="ok", detail="already running")

        result = ensure_service_running(conn, "fail2ban", restart=True)
        if not result.ok:
            operation = getattr(result.result, "operation_name", "") if result.result else ""
            failed_enable = "enable" in operation or "enable" in result.detail.lower()
            prefix = "failed to enable fail2ban" if failed_enable else "failed to start fail2ban"
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"{prefix}: {result.detail}",
            )

        return StepResult(
            name=self.name,
            status="changed" if result.changed else "ok",
            detail=result.detail or ("already running" if not result.changed else ""),
        )


class ConfigureBBR:
    """Enable BBR congestion control via sysctl."""

    name = "Enable BBR congestion control"

    def run(self, conn: ServerConnection, ctx: StepContext) -> StepResult:
        facts = ServerFacts(conn)
        # Check if BBR is already enabled
        if facts.sysctl("net.ipv4.tcp_congestion_control") == "bbr" and facts.sysctl("net.core.default_qdisc") == "fq":
            return StepResult(name=self.name, status="ok", detail="already enabled")

        # Apply sysctl settings
        for key, value in _BBR_SETTINGS.items():
            result = conn.run(f"sysctl -w {key}={value}", timeout=15)
            if result.returncode != 0:
                stderr = result.stderr.strip()
                # Containers and old kernels lack these tunables — warn, don't block deploy
                if "No such file" in stderr or "does not exist" in stderr:
                    return StepResult(
                        name=self.name,
                        status="changed",
                        detail=f"WARNING: {key} unavailable (kernel may not support BBR)",
                    )
                return StepResult(
                    name=self.name,
                    status="failed",
                    detail=f"sysctl {key} failed: {stderr[:200]}",
                )

        # Persist to sysctl.conf
        for key, value in _BBR_SETTINGS.items():
            # Remove existing entries and append new ones
            q_key = shlex.quote(key)
            q_value = shlex.quote(value)
            conn.run(f"sed -i '/^{q_key}/d' /etc/sysctl.conf", timeout=15)
            conn.run(f"printf '%s = %s\\n' {q_key} {q_value} >> /etc/sysctl.conf", timeout=15)

        return StepResult(name=self.name, status="changed")


class EnsurePort443:
    """Ensure port 443 is allowed if ufw is already active.

    Used when --no-harden skips full firewall configuration. Without this,
    a pre-existing firewall blocks the deployment silently.
    """

    name = "Ensure port 443"

    def run(self, conn: ServerConnection, ctx: ProvisionContext) -> StepResult:
        ufw = ServerFacts(conn).ufw_state()
        if not ufw.installed:
            return StepResult(name=self.name, status="ok", detail="ufw not installed")

        if not ufw.active:
            return StepResult(name=self.name, status="ok", detail="ufw not active")

        result = ensure_ufw_rule(conn, "allow 443/tcp")
        if not result.ok:
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"failed to allow HTTPS: {result.detail}",
            )
        detail_parts = []
        if result.changed:
            detail_parts.append("opened 443/tcp")

        # Hysteria2 UDP (always open — Hysteria2 is enabled by default)
        udp_result = ensure_ufw_rule(conn, "allow 443/udp")
        if udp_result.changed:
            detail_parts.append("opened 443/udp (Hysteria2)")

        return StepResult(
            name=self.name,
            status="changed" if detail_parts else "ok",
            detail=", ".join(detail_parts) if detail_parts else "already allowed",
        )


class ConfigureFirewall:
    """Configure UFW firewall rules."""

    name = "Configure firewall"

    def run(self, conn: ServerConnection, ctx: ProvisionContext) -> StepResult:
        commands: list = []
        facts = ServerFacts(conn)
        ufw = facts.ufw_state()
        ufw_active = ufw.active
        if not ufw.installed:
            install = ensure_packages(conn, ["ufw"], timeout=120)
            if not install.ok:
                return StepResult(
                    name=self.name,
                    status="failed",
                    detail="ufw not available — install it manually: apt-get install ufw",
                    commands=commands,
                )
            recheck = conn.run("which ufw", timeout=15)
            commands.append(recheck)
            if recheck.returncode != 0:
                return StepResult(
                    name=self.name,
                    status="failed",
                    detail="ufw not available — install it manually: apt-get install ufw",
                    commands=commands,
                )
            status = conn.run("ufw status", timeout=15)
            commands.append(status)
            ufw_active = status.returncode == 0 and "Status: active" in status.stdout

        changed = False

        # Allow the live sshd port(s) instead of assuming 22.
        for ssh_port in detect_ssh_ports(conn):
            result = ensure_ufw_rule(conn, f"allow {ssh_port}/tcp")
            if not result.ok:
                return StepResult(
                    name=self.name,
                    status="failed",
                    detail=f"failed to allow SSH port {ssh_port}: {result.detail}",
                    commands=commands,
                )
            if result.changed:
                changed = True

        # Allow HTTPS (port 443)
        result = ensure_ufw_rule(conn, "allow 443/tcp")
        if not result.ok:
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"failed to allow HTTPS: {result.detail}",
                commands=commands,
            )
        if result.changed:
            changed = True

        # Allow Hysteria2 UDP/443 (enabled by default — coexists with TCP/443)
        result = ensure_ufw_rule(conn, "allow 443/udp")
        if not result.ok:
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"failed to allow UDP/443: {result.detail}",
                commands=commands,
            )
        if result.changed:
            changed = True

        # Domain mode or hosted page: allow port 80 for ACME challenges
        if ctx.needs_web_server:
            result = ensure_ufw_rule(conn, "allow 80/tcp")
            if not result.ok:
                return StepResult(
                    name=self.name,
                    status="failed",
                    detail=f"failed to allow HTTP: {result.detail}",
                    commands=commands,
                )
            if result.changed:
                changed = True
        else:
            # Cleanup stale port 80 rule if switching from domain/hosted mode
            result = ensure_ufw_rule(conn, "delete allow 80/tcp 2>/dev/null")
            if result.ok and result.result and "Could not" not in result.result.stdout and result.changed:
                changed = True

        # Do not delete arbitrary user-managed rules. Meridian only owns the
        # standard public ports it opens itself (22/80/443), so cleanup is
        # limited to the stale port-80 rule above when web serving is disabled.

        # Set default policies and enable
        default_incoming = conn.run("ufw default deny incoming", timeout=15)
        commands.append(default_incoming)
        if default_incoming.returncode != 0:
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"ufw default deny incoming failed: {default_incoming.stderr.strip()[:200]}",
                commands=commands,
            )
        default_outgoing = conn.run("ufw default allow outgoing", timeout=15)
        commands.append(default_outgoing)
        if default_outgoing.returncode != 0:
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"ufw default allow outgoing failed: {default_outgoing.stderr.strip()[:200]}",
                commands=commands,
            )

        # Enable ufw (non-interactive) -- only counts as changed if it wasn't active
        if not ufw_active:
            enable = conn.run("ufw --force enable", timeout=30)
            commands.append(enable)
            if enable.returncode != 0:
                return StepResult(
                    name=self.name,
                    status="failed",
                    detail=f"ufw enable failed: {enable.stderr.strip()[:200]}",
                    commands=commands,
                )
            changed = True
        else:
            # Reload to apply any rule changes
            reload_result = conn.run("ufw reload", timeout=30)
            commands.append(reload_result)

        return StepResult(
            name=self.name,
            status="changed" if changed else "ok",
            commands=commands,
        )
