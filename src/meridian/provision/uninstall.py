"""Uninstall provisioning steps.

Removes proxy components but leaves Docker engine and system packages intact.
"""

from __future__ import annotations

from meridian.provision.steps import ProvisionContext, StepResult
from meridian.ssh import ServerConnection


class Uninstall:
    """Remove all Meridian proxy components from the server.

    Removes: Remnawave containers (panel, node, db, redis), Docker volumes,
    nginx Meridian configs, TLS certificates, web files, cron jobs,
    server credentials, CLI symlink, UFW rules.

    Also cleans up HAProxy/Caddy configs from earlier Meridian deployments.

    Does NOT remove: Docker engine, system packages, SSH settings.
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
            # Docker volumes (may remain after compose down)
            "docker volume rm valkey-socket remnawave-db-data 2>/dev/null; true",
            # nginx (+ systemd restart override)
            "systemctl stop nginx 2>/dev/null; systemctl disable nginx 2>/dev/null; true",
            "rm -f /etc/nginx/conf.d/meridian-http.conf /etc/nginx/stream.d/meridian.conf",
            "rm -rf /etc/nginx/stream.d/relay-maps",
            "rm -rf /etc/systemd/system/nginx.service.d",
            # TLS certificates
            "rm -rf /etc/ssl/meridian",
            # Legacy: HAProxy (from older deployments)
            "systemctl stop haproxy 2>/dev/null; systemctl disable haproxy 2>/dev/null; true",
            "rm -f /etc/haproxy/haproxy.cfg",
            "rm -rf /etc/systemd/system/haproxy.service.d",
            # Legacy: Caddy (from older deployments)
            "systemctl stop caddy 2>/dev/null; systemctl disable caddy 2>/dev/null; true",
            "rm -f /etc/caddy/conf.d/meridian.caddy /etc/caddy/Caddyfile",
            "rm -rf /etc/systemd/system/caddy.service.d",
            "rm -rf /var/lib/caddy/.local/share/caddy",
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
            # Server credentials and scripts
            "rm -rf /etc/meridian /root/meridian",
            # CLI symlink
            "rm -f /usr/local/bin/meridian",
            # Systemd daemon-reload after removing overrides
            "systemctl daemon-reload 2>/dev/null; true",
            # UFW rules
            "ufw delete allow 443/tcp 2>/dev/null; true",
            "ufw delete allow 80/tcp 2>/dev/null; true",
            # fail2ban
            "systemctl stop fail2ban 2>/dev/null; systemctl disable fail2ban 2>/dev/null; true",
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
