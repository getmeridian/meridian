"""Adopt the managed HTTP-01 listener without changing a valid certificate or another site."""

from __future__ import annotations

import shlex

from meridian.provision.ensure import ensure_file_content
from meridian.provision.nginx import DISABLE_STOCK_NGINX_SITE
from meridian.provision.nginx_render import private_listener_logs
from meridian.reconciler.resources import ResourceAction, ResourceReconcileError
from meridian.reconciler.server_evidence import file_content_or_absent
from meridian.reconciler.server_retirement import ACME_CHALLENGE_PATH
from meridian.reconciler.server_support import raise_if_timeout
from meridian.ssh import ServerConnection

# Older hosts can have this listener even though the certificate is valid and needs no renewal.
LEGACY_CHALLENGE = (
    "# Managed by Meridian v4 for ACME HTTP-01.\n"
    "server {\n"
    "    listen 80 default_server;\n"
    "    listen [::]:80 default_server;\n"
    "    server_name _;\n"
    "    location /.well-known/acme-challenge/ { root /var/www/acme; }\n"
    "    location / { return 404; }\n"
    "}\n"
)
PRIVATE_CHALLENGE = LEGACY_CHALLENGE.replace("    server_name _;", f"{private_listener_logs()}\n    server_name _;")
# Kept on disk until the validated reload succeeds. Losing SSH between write and reload must not report success.
PENDING_CHALLENGE = PRIVATE_CHALLENGE + "# Meridian: awaiting nginx reload.\n"


def read_challenge(conn: ServerConnection, action: ResourceAction) -> str | None:
    content = file_content_or_absent(
        action.resource.logical_id, conn.get_text(ACME_CHALLENGE_PATH, timeout=15), "ACME HTTP-01 listener"
    )
    if content is not None and content.startswith("# Managed by Meridian v4 for ACME HTTP-01.\n"):
        if content not in (LEGACY_CHALLENGE, PENDING_CHALLENGE, PRIVATE_CHALLENGE):
            raise ResourceReconcileError(
                "The managed ACME listener was customized; its logging policy could not be verified.",
                hint="Review the listener before reapplying. Keep operator sites in separate nginx files.",
            )
    return content


def write_challenge(conn: ServerConnection, action: ResourceAction, content: str) -> None:
    result = ensure_file_content(conn, ACME_CHALLENGE_PATH, content, create_parent=True)
    if not result.ok:
        raise_if_timeout(action, result.result)
        raise ResourceReconcileError("Could not write the managed ACME HTTP-01 listener.")


def check_challenge_paths(conn: ServerConnection, action: ResourceAction) -> None:
    # An operator may be serving challenges independently. Never replace a symlink to their site or seize its name.
    conf = shlex.quote(ACME_CHALLENGE_PATH)
    link = shlex.quote(ACME_CHALLENGE_PATH.replace("/sites-available/", "/sites-enabled/"))
    check = conn.run(
        f"test ! -L {conf} && {{ {{ test ! -e {link} && test ! -L {link}; }} || "
        f'{{ test -L {link} && test "$(readlink -f {link})" = {conf}; }}; }}',
        timeout=15,
    )
    if check.returncode:
        raise_if_timeout(action, check)
        raise ResourceReconcileError(
            "The ACME listener path points to a different site; its configuration was left alone."
        )


def enable_challenge(conn: ServerConnection, action: ResourceAction, *, reload_command: str) -> None:
    previous = read_challenge(conn, action)
    if previous not in (None, PRIVATE_CHALLENGE):
        raise ResourceReconcileError("An unrecognized ACME listener was left alone; review the existing site.")
    check_challenge_paths(conn, action)
    conf = shlex.quote(ACME_CHALLENGE_PATH)
    link = shlex.quote(ACME_CHALLENGE_PATH.replace("/sites-available/", "/sites-enabled/"))
    existing_link = conn.run(f"test -L {link}", timeout=15)
    if existing_link.returncode not in (0, 1):
        raise_if_timeout(action, existing_link)
        raise ResourceReconcileError("Could not inspect the ACME listener's enabled site.")
    write_challenge(conn, action, PRIVATE_CHALLENGE)
    for command in (f"{DISABLE_STOCK_NGINX_SITE} && ln -sfn {conf} {link}", "nginx -t", reload_command):
        result = conn.run(command, timeout=30)
        if not result.returncode:
            continue
        raise_if_timeout(action, result)
        # A conflicting operator default listener must not leave nginx unable to restart.
        if existing_link.returncode == 1:
            conn.run(f'test ! -L {link} || {{ test "$(readlink -f {link})" = {conf} && rm -f {link}; }}', timeout=15)
        if previous is None and read_challenge(conn, action) == PRIVATE_CHALLENGE:
            conn.run(f"rm -f {conf}", timeout=15)
        raise ResourceReconcileError(
            "Could not start the ACME HTTP-01 challenge listener.",
            hint="Check for another default listener on port 80, then run meridian apply again.",
        )


def adopt_challenge_logging(conn: ServerConnection, action: ResourceAction, *, reload_command: str) -> bool:
    current = read_challenge(conn, action)
    if current not in (LEGACY_CHALLENGE, PENDING_CHALLENGE):
        return False
    check_challenge_paths(conn, action)
    write_challenge(conn, action, PENDING_CHALLENGE)
    for command in ("nginx -t", reload_command):
        result = conn.run(command, timeout=30)
        if result.returncode:
            raise_if_timeout(action, result)
            write_challenge(conn, action, LEGACY_CHALLENGE)
            raise ResourceReconcileError(
                "The ACME listener's logging change has not been applied.",
                hint="Check nginx configuration and service health, then run meridian apply again.",
            )
    write_challenge(conn, action, PRIVATE_CHALLENGE)
    return True
