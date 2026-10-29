"""Uninstall provisioning steps.

Removes proxy components but leaves Docker engine and system packages intact.
"""

from __future__ import annotations

import shlex

from meridian.host_runtime import (
    AGENT_BINARY,
    AGENT_CONFIG_DIR,
    AGENT_LIB_DIR,
    AGENT_STATE_DIR,
    AGENT_UNIT,
    CONTAINERS,
    CONTROL_SERVICE_CONFIG,
    CONTROL_SERVICE_DIR,
    CONTROL_SERVICE_NAME,
    CONTROL_SERVICE_STATE_DIR,
    CONTROL_SERVICE_UNIT,
    CORE_UNIT,
    LEGACY_NODE_DIR,
    LEGACY_PANEL_DIR,
    LEGACY_PANEL_NETWORK,
    LEGACY_PANEL_SOCKET_VOLUME,
    NODE_DIR,
    PANEL_DIR,
    PANEL_PROJECT,
)
from meridian.provision.nginx import LEGACY_PROXY_CLEANUP
from meridian.provision.steps import ProvisionContext, StepResult
from meridian.ssh import ServerConnection

# Meridian deletes Debian's stock default site on install, so any enabled config left after
# its own files are gone belongs to the operator (nginx.org's stock conf.d/default.conf aside):
# keep nginx serving it. Only an nginx that served nothing but Meridian is stopped.
NGINX_STOP_UNLESS_SHARED = (
    "if ls /etc/nginx/sites-enabled/* /etc/nginx/conf.d/*.conf /etc/nginx/stream.d/*.conf 2>/dev/null"
    " | grep -qv '^/etc/nginx/conf.d/default.conf$'; then"
    " systemctl reload nginx 2>/dev/null;"
    " else systemctl stop nginx 2>/dev/null; systemctl disable nginx 2>/dev/null; fi; true"
)


def _compose_removal(*directories: str) -> list[str]:
    """Bring each Compose project down (images and volumes too), then delete its directory."""
    commands = []
    for directory in directories:
        quoted = shlex.quote(directory)
        commands += [f"cd {quoted} && docker compose down --rmi all -v 2>/dev/null; true", f"rm -rf {quoted}"]
    return commands


def _remove_containers(*names: str) -> str:
    """Remove containers a missing or edited compose file no longer names."""
    return f"docker rm -f {' '.join(shlex.quote(name) for name in names)} >/dev/null 2>&1; true"


class Uninstall:
    """Remove all Meridian proxy components from the server.

    Removes: Remnawave containers (panel, node, db, redis) under their neutral
    and pre-rename names, Docker volumes,
    nginx Meridian configs, Realm units, TLS certificates, web files, cron
    jobs, server credentials, CLI symlink, UFW rules (including tagged V4
    rules).

    Also cleans up 3x-ui and HAProxy/Caddy from earlier Meridian deployments.

    Does NOT remove: Docker engine, system packages, or the host hardening
    (SSH settings, fail2ban and its sshd jail), and keeps nginx running when
    the operator's own sites remain.
    """

    name = "Uninstall Meridian"

    def run(self, conn: ServerConnection, ctx: ProvisionContext) -> StepResult:
        commands = [
            # Remnawave panel stack and node, under their neutral and pre-rename names
            *_compose_removal(PANEL_DIR, LEGACY_PANEL_DIR, NODE_DIR, LEGACY_NODE_DIR),
            _remove_containers(*(name for container in CONTAINERS for name in (container.name, container.legacy))),
            # Meridian 3.x panel (3x-ui), so teardown also clears pre-4.0 deployments
            "cd /opt/3x-ui && docker compose down --rmi all 2>/dev/null; true",
            "rm -rf /opt/3x-ui",
            # Realm transparent relay (single relay, per-hop units, and their pre-rename names)
            "systemctl stop meridian-relay 2>/dev/null; systemctl disable meridian-relay 2>/dev/null; true",
            "rm -f /etc/systemd/system/meridian-relay.service /usr/local/bin/realm /etc/meridian/realm.toml",
            (
                "for unit in /etc/systemd/system/netfwd-*.service /etc/systemd/system/meridian-realm-*.service; do"
                ' [ -e "$unit" ] || continue; name=$(basename "$unit" .service);'
                ' systemctl stop "$name" 2>/dev/null; systemctl disable "$name" 2>/dev/null; rm -f "$unit";'
                " done; true"
            ),
            "rm -rf /usr/local/bin/netfwd /etc/netfwd",
            # Meridian's own node agent first (it would restart the core), then the core and the control service
            *(
                f"systemctl stop {unit} 2>/dev/null; systemctl disable {unit} 2>/dev/null; true"
                for unit in (AGENT_UNIT, CORE_UNIT, CONTROL_SERVICE_UNIT)
            ),
            (
                f"rm -rf /etc/systemd/system/{AGENT_UNIT} /etc/systemd/system/{CORE_UNIT}"
                f" /etc/systemd/system/{CONTROL_SERVICE_UNIT} {AGENT_BINARY} {AGENT_CONFIG_DIR} {AGENT_STATE_DIR}"
                f" {AGENT_LIB_DIR} {CONTROL_SERVICE_DIR} {CONTROL_SERVICE_CONFIG.rsplit('/', 1)[0]}"
                f" {CONTROL_SERVICE_STATE_DIR} {CONTROL_SERVICE_STATE_DIR}.attestation"
            ),
            f"userdel {CONTROL_SERVICE_NAME} 2>/dev/null; true",
            # Docker objects that may remain after compose down (pre-rename projects took the directory name)
            (
                f"docker volume rm {PANEL_PROJECT}_valkey-socket {LEGACY_PANEL_SOCKET_VOLUME}"
                " valkey-socket remnawave-db-data 2>/dev/null; true"
            ),
            f"docker network rm {PANEL_PROJECT}_internal {LEGACY_PANEL_NETWORK} 2>/dev/null; true",
            # nginx: Meridian's files and systemd restart override, then the service (see NGINX_STOP_UNLESS_SHARED)
            "rm -f /etc/nginx/conf.d/meridian-http.conf /etc/nginx/stream.d/meridian.conf",
            "rm -rf /etc/nginx/stream.d/relay-maps",
            (
                "rm -f /etc/nginx/stream.d/meridian-v4-*.conf"
                " /etc/nginx/sites-available/meridian-v4-*.conf /etc/nginx/sites-enabled/meridian-v4-*.conf"
            ),
            "rm -rf /etc/systemd/system/nginx.service.d",
            NGINX_STOP_UNLESS_SHARED,
            # TLS certificates
            "rm -rf /etc/ssl/meridian",
            # HAProxy/Caddy from older Meridian releases (marker-gated)
            LEGACY_PROXY_CLEANUP,
            # Web files
            "rm -rf /var/www/private /var/www/acme",
            # Cron jobs (stats + health watchdog + acme.sh renewal)
            (
                "crontab -l 2>/dev/null"
                " | grep -v 'update-stats.py'"
                " | grep -v 'health-check.sh'"
                " | grep -v 'acme.sh'"
                " | crontab - 2>/dev/null; true"
            ),
            # Server credentials, scripts, and V4 baseline attestations
            "rm -rf /etc/meridian /root/meridian /var/lib/meridian /var/lib/host-baseline",
            # Systemd daemon-reload after removing units and overrides
            "systemctl daemon-reload 2>/dev/null; true",
            # UFW rules
            "ufw delete allow 443/tcp 2>/dev/null; true",
            "ufw delete allow 80/tcp 2>/dev/null; true",
            "ufw delete allow 443/udp 2>/dev/null; true",
            # V4 rules carry an fw-<token> (earlier: meridian-v4-<token>) comment; delete highest
            # number first so numbering stays valid. SSH rules come from the baseline and are untagged.
            (
                "ufw status numbered 2>/dev/null"
                r" | sed -n -e 's/^\[ *\([0-9][0-9]*\)\].*# fw-[0-9a-f]\{16\}$/\1/p'"
                r" -e 's/^\[ *\([0-9][0-9]*\)\].*# meridian-v4-[0-9a-f]\{16\}$/\1/p'"
                ' | sort -rn | while read -r rule; do ufw --force delete "$rule"; done; true'
            ),
        ]

        errors = []
        for cmd in commands:
            result = conn.run(cmd, timeout=30)
            if result.returncode != 0 and "true" not in cmd:
                errors.append(f"{cmd}: {result.stderr.strip()[:100]}")

        if errors:
            return StepResult(
                name=self.name,
                status="failed",
                detail="; ".join(errors[:3]),
            )

        return StepResult(name=self.name, status="changed", detail="all components removed")
