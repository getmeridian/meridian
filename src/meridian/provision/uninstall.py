"""Uninstall provisioning steps.

Removes proxy components but leaves Docker engine and system packages intact.
"""

from __future__ import annotations

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


class Uninstall:
    """Remove all Meridian proxy components from the server.

    Removes: Remnawave containers (panel, node, db, redis), Docker volumes,
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
            # Remnawave panel stack (backend + PostgreSQL + Valkey)
            "cd /opt/remnawave && docker compose down --rmi all -v 2>/dev/null; true",
            "rm -rf /opt/remnawave",
            # Remnawave node
            "cd /opt/remnanode && docker compose down --rmi all -v 2>/dev/null; true",
            "rm -rf /opt/remnanode",
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
            # Docker volumes (may remain after compose down)
            "docker volume rm valkey-socket remnawave-db-data 2>/dev/null; true",
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
