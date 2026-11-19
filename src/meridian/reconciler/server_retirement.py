"""Remove server-side artifacts for resources that left the reviewed plan."""

from __future__ import annotations

import re
import shlex
from typing import Any

from meridian.cluster import ManagedResourceBinding
from meridian.compiler.models import (
    CertificatePayload,
    ControlServicePayload,
)
from meridian.host_runtime import IP_CERTIFICATE_DIRECTORY, certificate_directory
from meridian.reconciler.resources import ResourceReconcileError
from meridian.reconciler.server_render import (
    legacy_firewall_marker,
    legacy_realm_config_path,
    legacy_realm_service_name,
    realm_config_path,
    realm_service_name,
    unit_path,
)
from meridian.ssh import ServerConnection

_FIREWALL_MARKER = re.compile(r"(?:fw|meridian-v4)-[0-9a-f]{16}")
_CERTIFICATE = re.compile(r"/etc/ssl/meridian(?:/certs/[0-9a-f]{16})?/fullchain\.pem")
ACME_CHALLENGE_PATH = "/etc/nginx/sites-available/meridian-v4-acme.conf"
_NGINX_ARTIFACT = re.compile(
    r"/etc/nginx/(stream\.d|sites-available|meridian-v4-locations)/meridian-v4-[0-9a-f]{16}\.conf"
)


def _connection(context: Any, binding: ManagedResourceBinding) -> ServerConnection:
    if not binding.server_ref:
        raise ResourceReconcileError(
            f"{binding.logical_id} was recorded before Meridian tracked its server; remove it manually.",
        )
    connection: ServerConnection = context.connection(binding.server_ref)
    return connection


def delete_rules_by_comment(conn: ServerConnection, marker: str) -> bool:
    """Delete every UFW rule whose comment is exactly ``marker``; True when the deletion completed."""
    if not _FIREWALL_MARKER.fullmatch(marker):
        raise ResourceReconcileError(f"Refusing to delete firewall rules by unexpected comment {marker!r}.")
    # Highest number first so the remaining rule numbers stay valid.
    result = conn.run(
        "ufw status numbered 2>/dev/null"
        rf" | sed -n 's/^\[ *\([0-9][0-9]*\)\].*# {marker}$/\1/p'"
        ' | sort -rn | while read -r rule; do ufw --force delete "$rule" >/dev/null || exit 1; done',
        timeout=30,
    )
    return result.returncode == 0


class FirewallRetirement:
    """Delete UFW rules carrying this resource's comment, current or pre-rename."""

    context: Any

    def retire(self, binding: ManagedResourceBinding) -> None:
        if not _FIREWALL_MARKER.fullmatch(binding.remote_id):
            raise ResourceReconcileError(f"Unexpected firewall marker for {binding.logical_id}: {binding.remote_id!r}")
        conn = _connection(self.context, binding)
        markers = dict.fromkeys((binding.remote_id, legacy_firewall_marker(binding.logical_id)))
        if not all(delete_rules_by_comment(conn, marker) for marker in markers):
            raise ResourceReconcileError(f"Could not delete firewall rule {binding.logical_id}.")


class NginxArtifactRetirement:
    """Delete this resource's nginx file (and its enabled link), then reload nginx."""

    context: Any

    def retire(self, binding: ManagedResourceBinding) -> None:
        path = binding.remote_id
        if not _NGINX_ARTIFACT.fullmatch(path):
            raise ResourceReconcileError(f"Unexpected nginx artifact path for {binding.logical_id}: {path!r}")
        enabled = path.replace("/sites-available/", "/sites-enabled/")
        conn = _connection(self.context, binding)
        result = conn.run(
            f"rm -f {shlex.quote(path)} {shlex.quote(enabled)} && nginx -t 2>&1 && systemctl reload-or-restart nginx",
            timeout=30,
        )
        if result.returncode != 0:
            raise ResourceReconcileError(f"nginx did not reload after removing {path}: {result.stdout.strip()[:200]}")


class CertificateRetirement:
    """Stop renewing a certificate no Host uses any more and delete its files.

    acme.sh records where it installs each certificate, so the renewal entry is found
    from the binding's ``fullchain.pem`` path, even for a hashed domain directory. Nothing is
    touched while the control service there serves the same files (its domain's, or the
    server's IP certificate without one) or another certificate in the plan has them. The
    ACME challenge listener goes once no certificate is left on the server.
    """

    context: Any

    def retire(self, binding: ManagedResourceBinding) -> None:
        fullchain = binding.remote_id
        if not _CERTIFICATE.fullmatch(fullchain):
            raise ResourceReconcileError(f"Unexpected certificate path for {binding.logical_id}: {fullchain!r}")
        conn = _connection(self.context, binding)
        kept = [
            resource.payload
            for resource in self.context.plan.resources
            if isinstance(resource.payload, CertificatePayload | ControlServicePayload)
            and resource.payload.server_ref == binding.server_ref
        ]
        directory = fullchain.rsplit("/", 1)[0]
        # A panel with its own domain no longer uses the IP certificate, which would otherwise
        # keep renewing after the last IP-addressed path left.
        if any(
            isinstance(payload, ControlServicePayload)
            and directory
            == (certificate_directory(payload.public_hostname) if payload.public_hostname else IP_CERTIFICATE_DIRECTORY)
            for payload in kept
        ):
            return
        if any(
            isinstance(payload, CertificatePayload) and certificate_directory(payload.hostname) == directory
            for payload in kept
        ):
            return
        remove_unused_certificate(conn, fullchain, keep_challenge=bool(kept))


# Inspect nginx's effective includes on the server. Never print the configuration (it can
# hold private routes), and keep renewal for indirect symlink or variable-based references.
_CERT_USAGE = r"""
import os, re, shlex, sys
base, chain = sys.argv[1:]
certs = {os.path.realpath(chain), os.path.realpath(os.path.join(os.path.dirname(chain), "key.pem"))}
under = os.path.realpath(base) + "/"
lexer = shlex.shlex(sys.stdin, posix=True, punctuation_chars=";{}")
lexer.whitespace_split = True
shared, cert_directive = False, False
try:
    for value in lexer:
        # Comments and quotes follow ordinary nginx lexical boundaries; delimiters
        # may be adjacent (e.g. two directives on one line). Preserve uncertain uses.
        if os.path.isabs(value):
            real = os.path.realpath(value)
            if real in certs:
                print("in-use")
                break
            if cert_directive and real.startswith(under):
                shared = True
        if cert_directive and ("$" in value or not os.path.isabs(value)):
            print("in-use")
            break
        cert_directive = bool(re.fullmatch(r"[a-z_]*certificate(?:_key)?", value))
    else:
        print("in-use" if cert_directive else "shared" if shared else "unused")
except ValueError:
    print("in-use")  # syntax the conservative lexer cannot interpret
"""


_RETIRE_CHALLENGE = r"""
import os, pathlib, subprocess, sys
conf, link = map(pathlib.Path, sys.argv[1:3])
repair = len(sys.argv) > 3
stage = conf.parents[1] / ".meridian-acme-retirement"
pairs = [(link, stage / "link"), (conf, stage / "conf")]
def exists(path): return os.path.lexists(path)
if not any(exists(src) or exists(dst) for src,dst in pairs): sys.exit(0)
if exists(stage) and (stage.is_symlink() or not stage.is_dir() or stage.stat().st_uid != os.geteuid()
                      or stage.stat().st_mode & 0o077):
    sys.exit("Unrecognized ACME listener staging directory.")
for src,dst in pairs:
    if exists(src) and exists(dst): sys.exit("ACME listener changed during retirement.")
    if not (exists(src) or exists(dst)): continue
    item = src if exists(src) else dst
    if src == conf and (item.is_symlink() or not item.is_file()
            or b"# Managed by Meridian v4 for ACME HTTP-01." not in item.read_bytes()[:2048]):
        sys.exit(0)  # owned by someone else
    if src == link and (not item.is_symlink() or (link.parent / os.readlink(item)).resolve() != conf):
        sys.exit(0)
if repair:
    for src,dst in reversed(pairs):
        if exists(dst): dst.rename(src)
    if stage.exists(): stage.rmdir()
    sys.exit(0)
def run(*args):
    return subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
stage.mkdir(mode=0o700,exist_ok=True)
try:
    for src,dst in pairs:
        if exists(src): src.rename(dst)
    valid = run("nginx","-t")
    active = run("systemctl","is-active","--quiet","nginx")
    if not valid or (active and not run("systemctl","reload","nginx")):
        sys.exit("Could not reload nginx without the ACME listener; its files were restored.")
except BaseException:
    for src,dst in reversed(pairs):
        if exists(dst) and not exists(src): dst.rename(src)
    if run("systemctl","is-active","--quiet","nginx") and run("nginx","-t"):
        run("systemctl","reload","nginx")
    raise
for _,dst in pairs: dst.unlink(missing_ok=True)
stage.rmdir()
"""


def remove_unused_certificate(conn: ServerConnection, fullchain: str, *, keep_challenge: bool) -> None:
    """Remove renewal/files only once effective nginx has released them, including on a shared host."""
    if not _CERTIFICATE.fullmatch(fullchain):
        raise ResourceReconcileError(f"Unexpected Meridian certificate path: {fullchain!r}")
    directory = fullchain.rsplit("/", 1)[0]
    usage = shlex.join(("python3", "-c", _CERT_USAGE, IP_CERTIFICATE_DIRECTORY, fullchain))
    check = (
        "used=unused; if command -v nginx >/dev/null; then "
        "dump=$(mktemp) || exit 1; trap 'rm -f \"$dump\"' EXIT; "
        'nginx -T > "$dump" 2>/dev/null || { echo "nginx configuration cannot be validated" >&2; exit 1; }; '
        f'used=$({usage} < "$dump") || exit 1; fi; '
        '[ "$used" != in-use ] || exit 0; '
    )
    record = shlex.quote(f"Le_RealFullChainPath='{fullchain}'")
    files = f"{shlex.quote(fullchain)} {shlex.quote(directory + '/key.pem')}"
    command = check
    if not keep_challenge:
        enabled = ACME_CHALLENGE_PATH.replace("/sites-available/", "/sites-enabled/")
        challenge = shlex.join(("python3", "-c", _RETIRE_CHALLENGE, ACME_CHALLENGE_PATH, enabled))
        command = f"if [ -d /etc/nginx/.meridian-acme-retirement ]; then {challenge} repair || exit 1; fi; " + command
        command += f'if [ "$used" = unused ]; then {challenge} || exit 1; fi; '
    command += (
        'for conf in /root/.acme.sh/*/*.conf; do [ -f "$conf" ] || continue; '
        f'if grep -Fqx -- {record} "$conf"; then '
        'rm -rf -- "$(dirname "$conf")" || exit 1; else rc=$?; [ "$rc" -eq 1 ] || exit 1; fi; done;'
        f" rm -f {files} || exit 1"
    )
    if directory != IP_CERTIFICATE_DIRECTORY:
        command += (
            f"; if [ -d {shlex.quote(directory)} ]; then rmdir --ignore-fail-on-non-empty {shlex.quote(directory)}; fi"
        )
    result = conn.run(command, timeout=60, sensitive=True)
    if result.returncode != 0:
        raise ResourceReconcileError(
            f"Could not safely remove certificate {fullchain}; check the remaining nginx configuration."
        )


class RealmHopRetirement:
    """Stop and delete one hop's forwarder unit and config under both names; the binary stays for other hops."""

    context: Any

    def retire(self, binding: ManagedResourceBinding) -> None:
        current, legacy = realm_service_name(binding.logical_id), legacy_realm_service_name(binding.logical_id)
        if binding.remote_id not in {current, legacy}:
            raise ResourceReconcileError(f"Unexpected Realm service for {binding.logical_id}: {binding.remote_id!r}")
        conn = _connection(self.context, binding)
        services = f"{shlex.quote(current)} {shlex.quote(legacy)}"
        paths = (
            unit_path(current),
            unit_path(legacy),
            realm_config_path(binding.logical_id),
            legacy_realm_config_path(binding.logical_id),
        )
        # One unit per systemctl call: a multi-unit `disable --now` aborts entirely when
        # either unit is missing (a fresh relay has no legacy unit) and stops nothing.
        # Stopping again after daemon-reload catches a unit whose file is already gone.
        stop_each = (
            f"for unit in {services};"
            ' do systemctl disable --now "$unit" >/dev/null 2>&1; systemctl stop "$unit" >/dev/null 2>&1; done;'
        )
        result = conn.run(
            f"{stop_each}"
            f" rm -f {' '.join(shlex.quote(path) for path in paths)}"
            f" && systemctl daemon-reload && {stop_each}"
            f" systemctl reset-failed {services} >/dev/null 2>&1;"
            f" ! systemctl is-active --quiet {shlex.quote(current)}"
            f" && ! systemctl is-active --quiet {shlex.quote(legacy)}",
            timeout=60,
        )
        if result.returncode != 0:
            raise ResourceReconcileError(f"Realm service {binding.remote_id} is still running after removal.")
