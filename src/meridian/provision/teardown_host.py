"""Standalone, standard-library-only last-controller teardown. Runs on the target over SSH.

The public operation only recognises the controller recorded by this fleet. Unowned
sites, mixed legacy installations, ambiguous service definitions and paths are never
a reason to guess. Nothing is deleted until preflight and the final backup check pass.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import hmac
import json
import os
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from meridian.provision.teardown_journal import JournalError, Retirement
from meridian.provision.teardown_nginx import verify_nginx_runtime

NGINX = Path("/etc/nginx")
UNITS = Path("/etc/systemd/system")
CONFIG = Path("/etc/ctld/config.json")
STATE = Path("/var/lib/ctld")
TLS = Path("/etc/ssl/meridian")
ACME = Path("/root/.acme.sh")
CERTBOT = Path("/etc/letsencrypt/renewal")
DEFAULT_WEBROOT = Path("/usr/share/nginx/html")
MOUNTS = Path("/proc/self/mountinfo")
JOURNALS = Path("/var/lib")
BOOTSTRAP_FENCE = Path("/var/lib/meridian/fence.json")
UFW_FILES = (Path("/etc/ufw/user.rules"), Path("/etc/ufw/user6.rules"))
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
    result = subprocess.run(args, capture_output=True, text=True, env=dict(os.environ, LC_ALL="C"))
    if result.returncode:
        raise Refuse(f"{args[0]} {args[1]} did not succeed; inspect it on the VPS.")
    return result.stdout


def absolute(path: Path) -> None:
    if path != path.resolve():
        raise Refuse("A managed path is a symbolic link or passes through one; nothing will delete its target.")


@contextlib.contextmanager
def fence_file(path: Path) -> Iterator[int]:
    absolute(path)
    parent = path.parent.stat()
    if parent.st_uid != os.geteuid() or parent.st_mode & 0o022:
        raise Refuse("The SSH apply fence directory has an unverified owner or permissions. It was kept.")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_uid != os.geteuid() or st.st_mode & 0o022:
            raise Refuse("The SSH apply fence or its lock is not a private, owned file. It was kept.")
        yield fd
    finally:
        os.close(fd)


@contextlib.contextmanager
def lock_ssh_fence(*, preflight: bool) -> Iterator[int | None]:
    # The SSH writer flocks this inode around its compare-and-swap. Never replace the lock,
    # and never create an object during preflight on an installation without an SSH fence.
    lock = BOOTSTRAP_FENCE.with_suffix(".json.lock")
    if not os.path.lexists(lock):
        if os.path.lexists(BOOTSTRAP_FENCE):
            raise Refuse("The SSH apply fence is missing its lock. Review it before teardown.")
        yield None
        return
    with fence_file(lock) as fd:
        try:
            fcntl.flock(fd, (fcntl.LOCK_SH if preflight else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Refuse("An apply is updating the SSH fence. Wait for it to finish and retry.") from exc
        yield fd


def read_ssh_fence(fleet: str, lock_fd: int | None) -> dict[str, Any] | None:
    if not os.path.lexists(BOOTSTRAP_FENCE):
        return None
    lock = BOOTSTRAP_FENCE.with_suffix(".json.lock")
    if lock_fd is None or not os.path.samestat(os.fstat(lock_fd), lock.lstat()):
        raise Refuse("The SSH apply fence lock changed. Nothing will adopt or remove the new fence.")
    with fence_file(BOOTSTRAP_FENCE) as fd:
        if os.fstat(fd).st_size > 16384:
            raise Refuse("The SSH apply fence is unrecognized. It was kept.")
        try:
            record = json.loads(os.read(fd, 16385))
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise Refuse("The SSH apply fence is unreadable. It was kept.") from exc
    # The handover record can predate the current DB generation. The live database proves
    # that generation; this file only has to belong to the retired fleet and have no SSH writer.
    if not isinstance(record, dict) or record.get("fleet_id") != fleet:
        raise Refuse("The SSH apply fence does not belong to this fleet. It was kept.")
    if (
        set(record) != {"fleet_id", "generation", "plan_hash", "lease_holder", "lease_expires_at"}
        or type(record["generation"]) is not int
        or record["generation"] < 0
        or not isinstance(record["plan_hash"], str)
    ):
        raise Refuse("The SSH apply fence is unrecognized. It was kept.")
    if (record["lease_holder"], record["lease_expires_at"]) not in (
        ("", ""),
        ("meridian-control-service", "9999-12-31T00:00:00+00:00"),
    ):
        raise Refuse("The SSH apply fence has another holder. Review it before teardown.")
    return record


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
    dump = command("/usr/sbin/nginx", "-c", "/etc/nginx/nginx.conf", "-T")
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


def check_database(path: Path, fleet: str, topology: list[str | int], generation: str, lock_fd: int | None) -> None:
    # Also run after ctld stops: a panel edit or fleet apply during review MUST survive.
    for part in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm"), Path(str(path) + "-journal")):
        if part == path or os.path.lexists(part):
            absolute(part)
            if not part.is_file() or part.stat().st_nlink != 1:
                raise Refuse("The control database or its journal has an external link. Its data was left alone.")
    ssh = read_ssh_fence(fleet, lock_fd)
    with contextlib.closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        fence = db.execute("SELECT record FROM apply_fence WHERE id=1").fetchone()
        if fence is not None:
            record = json.loads(fence[0])  # once the service took ownership, its record always wins
        elif (
            ssh
            and not ssh["lease_holder"]
            and ssh["generation"] > 0
            and re.fullmatch(r"[0-9a-f]{64}", ssh["plan_hash"])
        ):
            record = ssh  # first apply completed through SSH, before the service could hold its fence
        else:
            raise Refuse("Neither the service nor the SSH apply fence proves the reviewed topology. Its data was kept.")
        if (
            not isinstance(record, dict)
            or record.get("fleet_id") != fleet
            or [record.get("generation"), record.get("plan_hash")] != topology
            or record.get("lease_holder")
        ):
            raise Refuse(
                "The fleet topology changed or an apply is in progress. Use the latest managing copy and retry."
            )
        if generation in {"discard", "preflight"}:
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


def active(unit: str) -> bool:
    state = command("systemctl", "show", unit, "-p", "ActiveState", "--value").strip()
    if state not in {"active", "inactive", "failed"}:
        raise Refuse("A service is changing state. Wait and retry teardown.")
    return state == "active"


def persisted_rules(markers: set[str]) -> set[str]:
    stored: set[str] = set()
    for path in UFW_FILES:
        if not path.is_file():
            continue
        contents = path.read_bytes()
        # UFW stores tuple comments as hex on current versions, or literal text on older versions.
        stored.update(m for m in markers if m.encode() in contents or m.encode().hex().encode() in contents)
    return stored


def firewall_status(markers: set[str]) -> str:
    if not markers:
        return ""
    unavailable = "Tagged UFW rules remain stored but inactive. Review and remove those fleet rules before retrying."
    if not shutil.which("ufw"):
        if persisted_rules(markers):
            raise Refuse(unavailable)
        return ""
    status = command("ufw", "status", "numbered")
    if status.strip() == "Status: inactive":
        stored = persisted_rules(markers)
        for line in command("ufw", "show", "added").splitlines():
            words = shlex.split(line)
            if len(words) > 1 and words[-2] == "comment" and words[-1] in markers:
                stored.add(words[-1])
        if stored:
            raise Refuse(unavailable)
        return ""
    return status


def clean_firewall(markers: set[str]) -> None:
    status = firewall_status(markers)
    rules = [
        int(m[1])
        for line in status.splitlines()
        if (m := re.match(r"^\[\s*(\d+)\].*# (\S+)$", line)) and m[2] in markers
    ]
    for number in sorted(rules, reverse=True):
        command("ufw", "--force", "delete", str(number))
    if persisted_rules(markers):
        raise Refuse("A tagged rule is still stored by UFW. Review the tagged rules and retry.")


def effective_nginx() -> None:
    try:
        verify_nginx_runtime()
    except ValueError as exc:
        raise Refuse(
            "The running nginx unit, binary or configuration could not be verified. Nothing will stop it."
        ) from exc


def enabled(unit: str) -> bool:
    state = command("systemctl", "show", unit, "-p", "UnitFileState", "--value").strip()
    if state not in {"enabled", "disabled", "static"}:
        raise Refuse("A service has an uncertain startup state; review it before teardown.")
    return state == "enabled"


def recover_preparation(journal: Retirement, txn: dict[str, Any]) -> None:
    journal.restore(txn["artifacts"])
    command("systemctl", "daemon-reload")
    for unit in txn["units"]:
        if unit == "nginx":
            effective_nginx()
        elif not inspect_unit(unit, EXEC[unit]):
            raise Refuse("The original unit could not be restored. Inspect the private retirement journal.")
        if unit in txn["enabled"] and not enabled(unit):
            command("systemctl", "enable", unit)
    for u in ("ctld", "netcore", "netagent", "nginx"):
        if u in txn["active"]:
            if not active(u):
                command("systemctl", "start", u)
            elif u == "nginx":
                command("systemctl", "reload", "nginx")
    journal.clear()


def unchanged_ssh_fence(journal: Retirement, txn: dict[str, Any], *, committed: bool = False) -> None:
    if "ssh_fence" not in txn or (committed and not os.path.lexists(BOOTSTRAP_FENCE)):
        return  # a previous cleanup may already have staged or disposed the marker
    if journal.plan([BOOTSTRAP_FENCE]).get(str(BOOTSTRAP_FENCE)) != txn["ssh_fence"]:
        raise Refuse("The SSH apply fence changed during teardown. Its installation was kept.")


def finish_committed(journal: Retirement, txn: dict[str, Any]) -> None:
    try:
        journal.assert_no_replacement(txn["artifacts"])
        unchanged_ssh_fence(journal, txn, committed=True)
        clean_firewall(set(txn["markers"]))
        journal.dispose(txn["artifacts"])
        # Keep the bootstrap guard visible throughout reversible staging. Only now can a new
        # apply see an empty host. Retrying after a lost rename/dispose never touches a new fence.
        if (signature := txn.get("ssh_fence")) is not None:
            if os.path.lexists(BOOTSTRAP_FENCE):
                journal.move(BOOTSTRAP_FENCE, signature)
            journal.dispose({str(BOOTSTRAP_FENCE): signature})
        # A lost SSH reply can be retried; no admin token or people are in this completion receipt.
        journal.write({}, "complete")
    except (OSError, Refuse, JournalError) as exc:
        raise Refuse(
            "Retirement committed: the fleet has stopped, but cleanup is incomplete. "
            "Fix the reported cause and run the same teardown again. " + str(exc)
        ) from exc


def teardown(
    fleet: str,
    admin_hash: str,
    generation: str,
    cert_names: list[str],
    markers: list[str],
    fragments: list[str],
    topology: list[str | int],
    *,
    preflight: bool = False,
) -> str:
    scope = json.dumps(
        [fleet, admin_hash, sorted(cert_names), sorted(markers), sorted(fragments), topology], separators=(",", ":")
    )
    journal = Retirement(JOURNALS, scope)
    # Lock an existing directory; neither successful nor refused preflight creates a file.
    fd = os.open(JOURNALS, os.O_RDONLY)
    with contextlib.ExitStack() as locks:
        locks.callback(os.close, fd)
        try:
            fcntl.flock(fd, (fcntl.LOCK_SH if preflight else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Refuse("A teardown is already running on this host. Wait for it and retry.") from exc
        lock_fd = locks.enter_context(lock_ssh_fence(preflight=preflight)) if fleet else None
        pending = journal.read() if fleet else None
        if pending is not None:
            if pending["phase"] == "complete":
                if any(
                    os.path.lexists(p)
                    for p in (STATE, CONFIG, BOOTSTRAP_FENCE, *(UNITS / (u + ".service") for u in EXEC))
                ):
                    raise Refuse("A new controller, unit or SSH apply fence exists on the retired host. It was kept.")
                return "complete" if preflight else "retired"
            if preflight:
                return "pending"
            if pending["phase"] == "preparing":
                unchanged_ssh_fence(journal, pending)
                recover_preparation(journal, pending)
                raise Refuse("Recovered the interrupted preparation. The original fleet was kept. Run teardown again.")
            finish_committed(journal, pending)
            return "retired"
        return new_teardown(
            fleet, admin_hash, generation, cert_names, markers, fragments, topology, journal, preflight, lock_fd
        )


def new_teardown(
    fleet: str,
    admin_hash: str,
    generation: str,
    cert_names: list[str],
    markers: list[str],
    fragments: list[str],
    topology: list[str | int],
    journal: Retirement,
    preflight: bool,
    lock_fd: int | None,
) -> str:
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
        return (
            "ready" if preflight else "retired"
        )  # After node remove the shelf may still contain the empty server. No reason to touch the host.
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
    if not preflight and generation != "discard" and not generation.isdecimal():
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
    effective_nginx()
    inspect_sites(files)
    override = UNITS / "nginx.service.d/restart.conf"
    dropins = command("systemctl", "show", "nginx", "-p", "DropInPaths", "--value").split()
    if any(path != str(override) for path in dropins) or (
        os.path.lexists(override)
        and (override.is_symlink() or override.read_text() != "[Service]\nRestart=on-failure\nRestartSec=5\n")
    ):
        raise Refuse("nginx has an operator systemd override. Keep it serving and review the shared installation.")
    check_database(STATE / "control.db", fleet, topology, "preflight" if preflight else generation, lock_fd)
    previously_active = {u for u in [*units, "nginx"] if active(u)}
    startup = {u for u in [*units, "nginx"] if enabled(u)}
    if any(NGINX.glob(".meridian-teardown-*")):
        raise Refuse("An older teardown left staged nginx files. Restore them before retrying.")
    renewal_files: list[Path] = []
    for conf in renewals:
        renewal_files.extend(
            conf.parent / name
            for name in (
                conf.name,
                conf.stem + ".cer",
                conf.stem + ".key",
                conf.stem + ".csr",
                conf.stem + ".csr.conf",
                "ca.cer",
                "fullchain.cer",
            )
        )
    rest = [
        override,
        *(UNITS / (u + ".service") for u in units),
        *(p for cert in certs for p in (cert, cert.parent / "key.pem")),
        *renewal_files,
        *RUNTIMES,
    ]
    firewall_status(set(markers))  # refusal must not register a new backup device
    artifacts = journal.plan([*files, *rest, BOOTSTRAP_FENCE])
    fence_signature = artifacts.pop(str(BOOTSTRAP_FENCE), None)
    if preflight:
        return "ready"
    txn: dict[str, Any] = {
        "artifacts": artifacts,
        "ssh_fence": fence_signature,
        "units": [*units, "nginx"],
        "active": sorted(previously_active),
        "enabled": sorted(startup),
        "markers": markers,
    }
    journal.write(txn, "preparing")
    try:
        command("systemctl", "stop", "ctld")
        check_database(STATE / "control.db", fleet, topology, generation, lock_fd)
        for path in files:
            journal.move(path, artifacts[str(path)])
        inspect_sites([])
        command("/usr/sbin/nginx", "-c", "/etc/nginx/nginx.conf", "-t")
        if "nginx" in previously_active:
            command("systemctl", "reload", "nginx")
        for unit in ("netagent", "netcore", "nginx"):
            if unit in txn["units"]:
                command("systemctl", "stop", unit)
        for unit in startup:
            command("systemctl", "disable", unit)
        for path in dict.fromkeys(rest):
            if str(path) in artifacts:
                journal.move(path, artifacts[str(path)])
        command("systemctl", "daemon-reload")
        journal.write(txn, "committed")
    except BaseException:
        # Atomic rename can succeed even if the connection or subsequent directory fsync fails.
        # Read the durable boundary rather than trying to revive an already retired fleet.
        saved = journal.read()
        if saved and saved["phase"] == "committed":
            raise Refuse("Retirement committed; rerun the same teardown to finish cleanup.")
        try:
            recover_preparation(journal, txn)
        except (OSError, Refuse, JournalError) as exc:
            raise Refuse(
                "Retirement preparation was interrupted. The original files were kept in private staging. "
                "Fix the cause and rerun the same teardown to recover. " + str(exc)
            ) from exc
        raise
    finish_committed(journal, txn)
    return "retired"


def main() -> None:
    try:
        fleet, token_hash, generation, certs, markers, fragments, topology, mode = json.loads(sys.argv[1])
        result = teardown(
            fleet, token_hash, generation, certs, markers, fragments, topology, preflight=mode == "preflight"
        )
    except (Refuse, JournalError) as exc:
        sys.exit(str(exc))
    except (ValueError, KeyError, TypeError, sqlite3.Error, OSError):
        sys.exit("Could not verify or finish cleanup. Inspect the service and staged files on the VPS, then retry.")
    print(result)


if __name__ == "__main__":
    main()
