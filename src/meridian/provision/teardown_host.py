"""Standalone, standard-library-only last-controller teardown. Runs on the target over SSH.

The public operation only recognises the controller recorded by this fleet. Unowned
sites, mixed legacy installations, ambiguous service definitions and paths are never
a reason to guess. Nothing is deleted until preflight and the final backup check pass.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

NGINX = Path("/etc/nginx")
UNITS = Path("/etc/systemd/system")
CONFIG = Path("/etc/ctld/config.json")
STATE = Path("/var/lib/ctld")
TLS = Path("/etc/ssl/meridian")
ACME = Path("/root/.acme.sh")
CERTBOT = Path("/etc/letsencrypt/renewal")
DEFAULT_WEBROOT = Path("/usr/share/nginx/html")
MOUNTS = Path("/proc/self/mountinfo")
BOOTSTRAP = {
    "conf.d/meridian-http.conf": b"# Meridian Proxy Configuration",
    "stream.d/meridian.conf": b"# nginx SNI Router",
}
FRAGMENT = re.compile(r"meridian-v4-(?:[0-9a-f]{16}|acme)\.conf")
EXEC = {
    "ctld": "/opt/ctld/bin/meridian serve --config /etc/ctld/config.json",
    "netagent": "/usr/local/bin/netagent run --bootstrap /etc/netagent/bootstrap.json --state-dir /var/lib/netagent",
    "netcore": "/usr/local/lib/netagent/netcore run -c /etc/netagent/core.json",
}
LEGACY = ("/opt/3x-ui", "/opt/remnawave", "/opt/ctlsvc", "/opt/remnanode", "/opt/netagent")

RUNTIMES = [
    Path(p)
    for p in (
        "/etc/netagent",
        "/var/lib/netagent",
        "/usr/local/lib/netagent",
        "/usr/local/bin/netagent",
        "/opt/ctld",
        "/etc/ctld",
        str(STATE),
        str(STATE) + ".attestation",
    )
]


class Refuse(Exception):
    """Actionable diagnostic safe to send back over SSH."""


def command(*args: str) -> str:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise Refuse(f"{args[0]} {args[1]} did not succeed; inspect it on the VPS.")
    return result.stdout


def absolute(path: Path) -> None:
    if path != path.resolve():
        raise Refuse("A managed path is a symbolic link or passes through one; nothing will delete its target.")


def known_fragments(recorded: set[str]) -> list[Path]:
    owned = []
    for rel, signature in BOOTSTRAP.items():
        path = NGINX / rel
        if os.path.lexists(path):
            absolute(path)
            header = path.read_bytes()[:4096]
            if signature not in header or b"# Managed by Meridian" not in header:
                raise Refuse("An unrecognized file occupies a Meridian bootstrap nginx path.")
            owned.append(path)
    for folder in ("stream.d", "sites-available", "sites-enabled", "meridian-v4-locations"):
        for path in sorted((NGINX / folder).glob("meridian-v4-*.conf")):
            if not FRAGMENT.fullmatch(path.name):
                raise Refuse("An unrecognized file occupies a Meridian nginx path.")
            absolute(path.parent)
            candidate = NGINX / ("sites-available" if folder == "sites-enabled" else folder) / path.name
            if not (candidate == NGINX / "sites-available/meridian-v4-acme.conf" or str(candidate) in recorded):
                raise Refuse("An nginx artifact does not belong to the saved fleet. Review it before teardown.")
            if folder == "sites-enabled":
                if (
                    not path.is_symlink()
                    or path.resolve() != NGINX / "sites-available" / path.name
                    or not path.is_file()
                ):
                    raise Refuse("An enabled Meridian nginx file does not link to its expected source.")
            else:
                absolute(path)
                if not path.is_file() or not path.read_bytes()[:1024].startswith(b"# Managed by Meridian v4"):
                    raise Refuse("An unrecognized file occupies a Meridian nginx path.")
            owned.append(path)
    return owned


def pristine_default(path: Path) -> bool:
    if path != NGINX / "conf.d/default.conf" or path.is_symlink() or not path.is_file():
        return False
    records = subprocess.run(["dpkg-query", "-W", "-f=${Conffiles}", "nginx"], capture_output=True, text=True)
    digest = hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()
    if records.returncode or [str(path), digest] not in [line.split() for line in records.stdout.splitlines()]:
        return False
    # Owners sometimes serve real content with the stock vhost. The config hash alone is not ownership.
    if DEFAULT_WEBROOT.resolve() != DEFAULT_WEBROOT or not DEFAULT_WEBROOT.is_dir():
        return False
    manifest = subprocess.run(["dpkg-query", "--control-show", "nginx", "md5sums"], capture_output=True, text=True)
    if manifest.returncode:
        return False
    packaged = {
        parts[1]: parts[0] for line in manifest.stdout.splitlines() if len(parts := line.split(maxsplit=1)) == 2
    }
    for item in DEFAULT_WEBROOT.iterdir():
        expected = packaged.get(str(item).lstrip("/"))
        if (
            not expected
            or item.is_symlink()
            or not item.is_file()
            or item.stat().st_size > 1024 * 1024
            or expected != hashlib.md5(item.read_bytes(), usedforsecurity=False).hexdigest()
        ):
            return False
    return True


def inspect_sites(files: list[Path]) -> None:
    # nginx -T names every effective include, even outside /etc/nginx. Never return its contents.
    known = {p.resolve() for p in files}
    dump = command("nginx", "-T")
    if not dump.startswith("# configuration file "):
        raise Refuse("Cannot identify the effective nginx configuration.")
    for block in re.split(r"(?m)^# configuration file ", dump)[1:]:
        name, sep, body = block.partition(":\n")
        if not sep:
            raise Refuse("Cannot identify an nginx configuration section.")
        p = Path(name)
        if p.resolve() in known or pristine_default(p):
            continue
        lexer = shlex.shlex(body, posix=True, punctuation_chars=";{}")
        lexer.whitespace_split = True
        if any(token in {"listen", "server", "ssl_certificate", "ssl_certificate_key"} for token in lexer):
            raise Refuse("Another nginx site is configured. Leave it serving; separate its resources before teardown.")


def inspect_unit(unit: str, executable: str) -> bool:
    path = UNITS / (unit + ".service")
    state = command("systemctl", "show", unit + ".service", "-p", "LoadState", "--value").strip()
    if not os.path.lexists(path) and state == "not-found":
        return False
    absolute(path)
    if state != "loaded" or not path.is_file() or "ExecStart=" + executable not in path.read_text().splitlines():
        raise Refuse("A service name is in use by an unrecognized unit; no service was removed.")
    effective = command("systemctl", "show", unit + ".service", "-p", "FragmentPath", "--value").strip()
    dropins = command("systemctl", "show", unit + ".service", "-p", "DropInPaths", "--value").strip()
    if effective != str(path) or dropins:
        raise Refuse("A managed service has a custom definition or override; review it before teardown.")
    return True


def check_database(path: Path, fleet: str, topology: list[str | int], generation: str) -> None:
    # Also run after ctld stops: a panel edit or fleet apply during review MUST survive.
    with contextlib.closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        fence = db.execute("SELECT record FROM apply_fence WHERE id=1").fetchone()
        record = json.loads(fence[0]) if fence else {}
        if (
            record.get("fleet_id") != fleet
            or [record.get("generation"), record.get("plan_hash")] != topology
            or record.get("lease_holder")
        ):
            raise Refuse(
                "The fleet topology changed or an apply is in progress. Use the latest managing copy and retry."
            )
        if generation == "discard":
            return
        row = db.execute("SELECT changes FROM backup_progress WHERE id=1").fetchone()
        receipt = db.execute(
            "SELECT MAX(r.changes) FROM backup_receipts r JOIN backup_devices d USING (device_id) "
            "WHERE d.revoked_at IS NULL"
        ).fetchone()
        if row != (int(generation),) or receipt != (int(generation),):
            raise Refuse("The fleet changed after its backup was reviewed. Its database was kept. Run teardown again.")


def inspect_renewals(certs: set[Path]) -> list[Path]:
    if any(CERTBOT.glob("*.conf")):
        raise Refuse("There is a separate Certbot renewal. Keep its web server and challenge in place.")
    own = []
    for conf in ACME.glob("*/*.conf"):
        absolute(conf)
        contents = conf.read_text().splitlines()
        if not any(line.startswith("Le_Domain=") for line in contents):
            continue
        if not any(f"Le_RealFullChainPath='{chain}'" in contents for chain in certs):
            raise Refuse("Another ACME renewal uses this server. Keep its challenge and separate it before teardown.")
        own.append(conf)
    return own


def remove_file(path: Path) -> None:
    path.unlink(missing_ok=True)


def remove_tree(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)


def active(unit: str) -> bool:
    state = command("systemctl", "show", unit, "-p", "ActiveState", "--value").strip()
    if state not in {"active", "inactive", "failed"}:
        raise Refuse("A service is changing state. Wait and retry teardown.")
    return state == "active"


def clean_firewall(markers: set[str]) -> None:
    if not markers or not shutil.which("ufw"):
        return
    status = command("ufw", "status", "numbered")
    rules = [
        int(m[1])
        for line in status.splitlines()
        if (m := re.match(r"^\[\s*(\d+)\].*# (\S+)$", line)) and m[2] in markers
    ]
    for number in sorted(rules, reverse=True):
        command("ufw", "--force", "delete", str(number))


def teardown(
    fleet: str,
    admin_hash: str,
    generation: str,
    cert_names: list[str],
    markers: list[str],
    fragments: list[str],
    topology: list[str | int],
) -> None:
    if any(os.path.lexists(p) for p in LEGACY):
        raise Refuse(
            "This VPS also contains an older panel or container installation. Identify it before removing anything."
        )
    files = known_fragments(set(fragments))
    if not fleet:
        if files or any(os.path.lexists(p) for p in (CONFIG, STATE, *(UNITS / (u + ".service") for u in EXEC))):
            raise Refuse(
                "This host still contains a controller or managed services. Use their owning fleet for teardown."
            )
        return  # After node remove the shelf may still contain the empty server. No reason to touch the host.
    absolute(CONFIG)
    if not CONFIG.is_file():
        raise Refuse("Could not establish ownership of the controller. Nothing was removed.")
    settings = json.loads(CONFIG.read_text())
    if (
        not fleet
        or settings.get("fleet_id") != fleet
        or not hmac.compare_digest(settings.get("admin_token_sha256", ""), admin_hash)
    ):
        raise Refuse("The target is not the controller recorded by this fleet. Nothing was removed.")
    if Path(settings.get("state_dir", str(STATE))) != STATE:
        raise Refuse("This controller uses a custom state directory. Review the installation before teardown.")
    if generation != "discard" and not generation.isdecimal():
        raise Refuse("A verified backup or explicit discard is required.")
    if not all(re.fullmatch(r"(?:fw-|meridian-v4-)[0-9a-f]{16}", m) for m in markers):
        raise Refuse("The saved fleet has an unrecognized firewall binding.")
    certs = {Path(p) for p in cert_names}
    if not all(
        p == TLS / "fullchain.pem"
        or (
            p.parent.parent == TLS / "certs"
            and re.fullmatch(r"[0-9a-f]{16}", p.parent.name)
            and p.name == "fullchain.pem"
        )
        for p in certs
    ):
        raise Refuse("The saved fleet has an unrecognized certificate binding.")
    for mount in MOUNTS.read_text().splitlines():
        item = Path(re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), mount.split()[4]))
        if any(item == directory or directory in item.parents for directory in RUNTIMES):
            raise Refuse("A managed data directory contains a separate mounted filesystem. Review it before teardown.")
    for path in (*RUNTIMES, *(p for cert in certs for p in (cert, cert.parent / "key.pem"))):
        absolute(path)
    units = [u for u in EXEC if inspect_unit(u, EXEC[u])]
    if "netagent" not in units and any(p.exists() and "netagent" in p.parts for p in RUNTIMES):
        raise Refuse("Agent files have no owning unit. Review the host before teardown.")
    if "ctld" not in units:
        raise Refuse("Could not verify the installed control service.")
    if list(UNITS.glob("netfwd-*.service")) or list(UNITS.glob("meridian-realm-*.service")):
        raise Refuse("Retire relay roles through the reviewed fleet plan before teardown.")
    renewals = inspect_renewals(certs)
    inspect_sites(files)
    override = UNITS / "nginx.service.d/restart.conf"
    dropins = command("systemctl", "show", "nginx", "-p", "DropInPaths", "--value").split()
    if any(path != str(override) for path in dropins) or (
        os.path.lexists(override)
        and (override.is_symlink() or override.read_text() != "[Service]\nRestart=on-failure\nRestartSec=5\n")
    ):
        raise Refuse("nginx has an operator systemd override. Keep it serving and review the shared installation.")
    check_database(STATE / "control.db", fleet, topology, generation)
    previously_active = {u for u in [*units, "nginx"] if active(u)}
    # An interrupted SSH call must leave held files safe, never in a glob nginx serves.
    if any(NGINX.glob(".meridian-teardown-*")):
        raise Refuse(
            "An interrupted teardown has staged nginx files. Restore them from its private staging directory and retry."
        )
    can_remove_stage = False
    held: list[tuple[Path, Path]] = []
    stopped: list[str] = []
    stage = Path(tempfile.mkdtemp(prefix=".meridian-teardown-", dir=NGINX))
    try:
        try:
            command("systemctl", "stop", "ctld")
            stopped.append("ctld")
            check_database(STATE / "control.db", fleet, topology, generation)
            for path in files:
                dest = stage / path.parent.name / path.name
                dest.parent.mkdir(mode=0o700, exist_ok=True)
                path.rename(dest)
                held.append((path, dest))
            inspect_sites([])  # a newly enabled operator site must not be stopped
            command("nginx", "-t")
            if "nginx" in previously_active:
                command("systemctl", "reload", "nginx")
            for u in ("netagent", "netcore"):
                if u in units:
                    command("systemctl", "stop", u)
                    stopped.append(u)
            command("systemctl", "stop", "nginx")
            stopped.append("nginx")
            clean_firewall(set(markers))
        except BaseException:
            for path, dest in reversed(held):
                dest.rename(path)
            try:
                if held and "nginx" in previously_active and "nginx" not in stopped:
                    command("systemctl", "reload", "nginx")
            finally:
                for u in reversed(stopped):
                    if u in previously_active:
                        command("systemctl", "start", u)
            can_remove_stage = True
            raise
        for u in [*units, "nginx"]:
            command("systemctl", "disable", u)
        remove_file(override)
        for u in units:
            remove_file(UNITS / (u + ".service"))
        command("systemctl", "daemon-reload")
        for cert in certs:
            remove_file(cert)
            remove_file(cert.parent / "key.pem")
        for renewal in renewals:
            # Revoke only this ACME job and its known key material; other files may be operator records.
            for name in (
                renewal.name,
                renewal.stem + ".cer",
                renewal.stem + ".key",
                renewal.stem + ".csr",
                renewal.stem + ".csr.conf",
                "ca.cer",
                "fullchain.cer",
            ):
                remove_file(renewal.parent / name)
            if not any(renewal.parent.iterdir()):
                renewal.parent.rmdir()
        for path in RUNTIMES:
            if path.is_dir():
                remove_tree(path)
            else:
                remove_file(path)
        can_remove_stage = True
    finally:
        # Do not throw away the only copy if rollback itself failed.
        if can_remove_stage:
            remove_tree(stage)


def main() -> None:
    try:
        fleet, token_hash, generation, certs, markers, fragments, topology = json.loads(sys.argv[1])
        teardown(fleet, token_hash, generation, certs, markers, fragments, topology)
    except Refuse as exc:
        sys.exit(str(exc))
    except (ValueError, KeyError, TypeError, sqlite3.Error, OSError):
        sys.exit("Could not verify or finish cleanup. Inspect the service and staged files on the VPS, then retry.")
    print("retired")


if __name__ == "__main__":
    main()
