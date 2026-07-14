"""TLS certificate provisioning step via acme.sh.

Handles both domain certificates (90-day, standard webroot) and
IP certificates (6-day shortlived profile via Let's Encrypt).
Bootstrap self-signed cert is handled by ConfigureNginx in nginx.py.
"""

from __future__ import annotations

import re
import shlex
import time

from meridian.config import ACME_SERVER
from meridian.provision.ensure import resolve_ctx
from meridian.provision.steps import ProvisionContext, StepResult
from meridian.ssh import ServerConnection

# ---------------------------------------------------------------------------
# IssueTLSCert helpers
# ---------------------------------------------------------------------------

_SHORTLIVED_IP_CERT_RENEWAL_DAYS = 5
_SHORTLIVED_IP_CERT_MAX_NEXT_RENEW_SECONDS = 7 * 24 * 60 * 60


def _load_acme_domain_info(conn: ServerConnection, cert_host: str) -> str | None:
    """Return acme.sh domain config output, or None if this host is unknown."""
    q_cert_host = shlex.quote(cert_host)
    result = conn.run(
        f"/root/.acme.sh/acme.sh --info -d {q_cert_host} 2>/dev/null",
        timeout=30,
    )
    if result.returncode != 0 or "Le_Domain=" not in result.stdout:
        return None
    return result.stdout


def _read_acme_int(domain_info: str, key: str) -> int | None:
    """Extract an integer value from acme.sh domain config output."""
    match = re.search(rf"^{re.escape(key)}=['\"]?(\d+)['\"]?$", domain_info, re.MULTILINE)
    return int(match.group(1)) if match else None


def _stale_shortlived_policy(domain_info: str) -> bool:
    """Return True when stored acme renewal metadata is incompatible with 6-day IP certs."""
    renewal_days = _read_acme_int(domain_info, "Le_RenewalDays")
    if renewal_days is not None:
        return renewal_days != _SHORTLIVED_IP_CERT_RENEWAL_DAYS

    next_renew_time = _read_acme_int(domain_info, "Le_NextRenewTime")
    if next_renew_time is None:
        return True

    return next_renew_time > int(time.time()) + _SHORTLIVED_IP_CERT_MAX_NEXT_RENEW_SECONDS


# ---------------------------------------------------------------------------
# IssueTLSCert — issue real TLS certificate via acme.sh
# ---------------------------------------------------------------------------


class IssueTLSCert:
    """Issue a real TLS certificate via acme.sh and install it.

    Uses the webroot method against the running nginx. On failure, nginx
    continues running with a self-signed bootstrap cert — Reality VPN
    works regardless since it uses its own encryption.
    """

    name = "Issue TLS certificate"

    def __init__(
        self,
        domain: str,
        ip_mode: bool = False,
        server_ip: str | None = None,
        certificate_directory: str = "/etc/ssl/meridian",
    ) -> None:
        self.domain = domain
        self.ip_mode = ip_mode
        self.server_ip = server_ip
        self.certificate_directory = certificate_directory

    def run(self, conn: ServerConnection, ctx: ProvisionContext) -> StepResult:
        server_ip = resolve_ctx(self.server_ip, ctx.ip)
        cert_host = server_ip if self.ip_mode else self.domain
        q_cert_host = shlex.quote(cert_host)
        profile_flag = " --certificate-profile shortlived" if self.ip_mode else ""
        renew_days_flag = ""
        force_flag = ""

        if self.ip_mode:
            renew_days_flag = f" --days {_SHORTLIVED_IP_CERT_RENEWAL_DAYS}"
            domain_info = _load_acme_domain_info(conn, cert_host)
            if domain_info:
                # acme.sh defaults to a 30-day renew window, which is wrong
                # for LE's 6-day IP certs. Force a one-time migration reissue
                # when the stored policy is stale. When the policy is already
                # correct, let acme.sh decide whether to reissue and always
                # re-run --install-cert so teardown/redeploy can reuse the
                # existing cached cert without creating a new order.
                if _stale_shortlived_policy(domain_info):
                    force_flag = " --force"

        result = conn.run(
            f"/root/.acme.sh/acme.sh --issue -d {q_cert_host} "
            f"--webroot /var/www/acme --server {shlex.quote(ACME_SERVER)}"
            f"{profile_flag}{renew_days_flag}{force_flag} 2>&1",
            timeout=180,
        )
        # acme.sh returns 0 on success, 2 if cert already valid (skip renewal)
        cert_issued = result.returncode in (0, 2)

        if cert_issued:
            q_certificate_directory = shlex.quote(self.certificate_directory)
            directory = conn.run(
                f"mkdir -p {q_certificate_directory} && chmod 700 {q_certificate_directory}",
                timeout=15,
            )
            if directory.returncode != 0:
                return StepResult(
                    name=self.name,
                    status="failed",
                    detail=f"Failed to prepare TLS certificate directory for {cert_host}",
                )
            q_key_file = shlex.quote(f"{self.certificate_directory}/key.pem")
            q_fullchain_file = shlex.quote(f"{self.certificate_directory}/fullchain.pem")
            # Install cert and set reload command for auto-renewal
            install = conn.run(
                f"/root/.acme.sh/acme.sh --install-cert -d {q_cert_host} "
                f"--key-file {q_key_file} "
                f"--fullchain-file {q_fullchain_file} "
                f'--reloadcmd "systemctl reload nginx" 2>&1',
                timeout=60,
            )
            if install.returncode != 0:
                detail = install.stderr.strip() or install.stdout.strip() or "unknown error"
                return StepResult(
                    name=self.name,
                    status="failed",
                    detail=f"Failed to install TLS cert for {cert_host}: {detail[:200]}",
                )
            # Reload to pick up the real cert
            reload = conn.run("systemctl reload nginx", timeout=15)
            if reload.returncode != 0:
                detail = reload.stderr.strip() or reload.stdout.strip() or "unknown error"
                return StepResult(
                    name=self.name,
                    status="failed",
                    detail=f"Failed to reload nginx after TLS cert install for {cert_host}: {detail[:200]}",
                )

        if cert_issued:
            return StepResult(
                name=self.name,
                status="changed",
                detail=f"TLS cert issued for {cert_host}",
            )

        # ACME failed — server runs with self-signed cert.
        # Reality VPN works regardless (own encryption), but connection
        # pages will show browser cert warnings until resolved.
        return StepResult(
            name=self.name,
            status="changed",
            detail=(
                f"WARNING: TLS cert failed for {cert_host} — using self-signed. "
                "Connection pages will show cert warnings. "
                "Check port 80 is open and domain resolves correctly"
            ),
        )
