"""OS-level provisioning steps: packages, hardening, firewall, sysctl."""

from __future__ import annotations

import shlex
from importlib.resources import files

from meridian.facts import ServerFacts
from meridian.provision.ensure import ensure_file_content, ensure_packages, ensure_service_running, ensure_ufw_rule
from meridian.provision.steps import ProvisionContext, StepContext, StepResult
from meridian.ssh import ServerConnection

# Minimum disk space required (in MB)
MIN_DISK_SPACE_MB = 2048

# The control stack peaked near 1.25 GB of RAM plus swap on a 1 GB server; below
# this total, the panel's first start starved sshd until the deploy timed out.

# Packages required by the deployment stack
# Debian 12 logs only to the journal (no auth.log) while its fail2ban reads log files by
# default, so the sshd jail found nothing and fail2ban exited; the journal backend works on
# every supported release and needs the python3-systemd binding.
FAIL2BAN_PACKAGES = ["fail2ban", "python3-systemd"]
FAIL2BAN_JAIL_PATH = "/etc/fail2ban/jail.d/sshd-journal.local"
FAIL2BAN_JAIL_CONF = "[sshd]\nenabled = true\nbackend = systemd\n"

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
    "unattended-upgrades",
    "apt-listchanges",
    # Debian 13 keeps "dnsutils" only as a virtual name, which dpkg-query never reports installed.
    "bind9-dnsutils",
]

# Auto-upgrades config content
AUTO_UPGRADES_CONF = """\
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
"""

# BBR sysctl settings
_BBR_SETTINGS = {
    "net.core.default_qdisc": "fq",
    "net.ipv4.tcp_congestion_control": "bbr",
}


def detect_ssh_ports(conn: ServerConnection) -> list[int]:
    """Detect effective sshd listen ports plus the port this session arrived on.

    ``sshd -T`` misses socket-activated overrides (``ssh.socket``) and
    ``ListenAddress host:port`` forms; the live session port is the one port
    we know works, so it must never be firewalled before ``ufw enable``.
    """
    ports = set(ServerFacts(conn).ssh_ports())
    connected = getattr(conn, "port", None)
    if not getattr(conn, "local_mode", False) and isinstance(connected, int) and 0 < connected < 65536:
        ports.add(connected)
    return sorted(ports)


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
                detail=f"apt failed: {stderr}",
            )

        return StepResult(name=self.name, status="changed" if result.changed else "ok", detail=result.detail)


class EnableAutoUpgrades:
    """Enable automatic security updates via unattended-upgrades."""

    name = "Enable automatic security updates"

    def run(self, conn: ServerConnection, ctx: ProvisionContext) -> StepResult:
        result = ensure_file_content(conn, "/etc/apt/apt.conf.d/20auto-upgrades", AUTO_UPGRADES_CONF, mode="644")
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
    """Apply global sshd defaults without changing provider-specific configuration."""

    name = "Harden SSH configuration"

    def run(self, conn: ServerConnection, ctx: ProvisionContext) -> StepResult:
        if not conn.verify_fresh_key_login():
            return StepResult(
                name=self.name, status="failed", detail="A fresh SSH key login failed. SSH settings were kept."
            )
        source = files("meridian.provision").joinpath("ssh_hardening.py").read_text(encoding="utf-8")
        result = conn.run("python3 -", input=source, sensitive=True, timeout=90, operation_name=self.name)
        status = result.stdout.strip()
        if result.returncode or status not in {"changed", "ok"}:
            detail = (result.stderr.strip().splitlines() or ["Could not confirm the SSH hardening result."])[-1][:300]
            return StepResult(name=self.name, status="failed", detail=detail, commands=[result])
        return StepResult(name=self.name, status="ok" if status == "ok" else "changed", commands=[result])


class ConfigureFail2ban:
    """Enable fail2ban with sshd jail for brute-force protection.

    Assumes fail2ban is already installed by InstallPackages.
    """

    name = "Configure fail2ban"

    def run(self, conn: ServerConnection, ctx: StepContext) -> StepResult:
        jail = ensure_file_content(conn, FAIL2BAN_JAIL_PATH, FAIL2BAN_JAIL_CONF, mode="644")
        if not jail.ok:
            return StepResult(name=self.name, status="failed", detail=f"failed to write the sshd jail: {jail.detail}")
        active = conn.run("systemctl is-active fail2ban", timeout=15)
        if not jail.changed and active.returncode == 0 and active.stdout.strip() == "active":
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
        # fail2ban reports a successful start, then exits once it fails to load a jail.
        settled = conn.run("sleep 2; systemctl is-active fail2ban", timeout=20)
        if settled.returncode != 0 or settled.stdout.strip() != "active":
            log = conn.run("journalctl -u fail2ban -n 5 --no-pager -o cat", timeout=15)
            return StepResult(
                name=self.name,
                status="failed",
                detail=f"fail2ban stopped right after starting: {log.stdout.strip()[-300:]}",
            )

        return StepResult(
            name=self.name,
            status="changed",
            detail=result.detail or "restarted",
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


# Prints and deletes (highest number first) bare `443/udp ALLOW IN Anywhere` rules
# with no comment; tagged meridian-v4 rules and narrower operator rules stay.
_DELETE_UNTAGGED_UDP_443 = (
    "ufw status numbered 2>/dev/null"
    r" | sed -n 's/^\[ *\([0-9][0-9]*\)\] 443\/udp\( (v6)\)\{0,1\} *ALLOW IN *Anywhere\( (v6)\)\{0,1\} *$/\1/p'"
    ' | sort -rn | while read -r rule; do ufw --force delete "$rule" >/dev/null && echo "$rule"; done; true'
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
                    detail=f"ufw could not be installed: {install.detail}",
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

        # Earlier 4.0 betas let the control-plane bootstrap open an untagged
        # 443/udp rule even without Hysteria2; compiled rules always carry a
        # meridian-v4 comment, so only the untagged one is removed.
        stale = conn.run(_DELETE_UNTAGGED_UDP_443, timeout=30)
        commands.append(stale)
        if stale.returncode == 0 and stale.stdout.strip():
            changed = True

        # Do not delete arbitrary user-managed rules; the reviewed plan owns
        # every public port through tagged rules.

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
