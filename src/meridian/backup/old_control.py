"""Retire bootstrap ingress on the reachable, replaced controller during a reviewed restore."""

import shlex

from meridian.backup import BackupError
from meridian.host_runtime import CONTROL_SERVICE_CONFIG, CONTROL_SERVICE_UNIT, certificate_directory
from meridian.reconciler.server_retirement import remove_unused_certificate
from meridian.ssh import ServerConnection

# Runs on the original server with its installed system Python. Stage the two named
# bootstrap fragments outside nginx's *.conf globs; pending files survive a lost reply.
_DETACH = r"""
import hmac, json, os, pathlib, re, subprocess, sys
config, fleet, unit, replacement_token_hash = sys.argv[1:]
def fail(message):
    sys.exit(message)
fragments = [
    (pathlib.Path("/etc/nginx/conf.d/meridian-http.conf"), "# Meridian Proxy Configuration"),
    (pathlib.Path("/etc/nginx/stream.d/meridian.conf"), "# nginx SNI Router"),
]
def pending(path):
    return path.parents[1] / ".meridian-restore-pending" / path.name
if not os.path.lexists(config):
    fail("Cannot verify ownership: check the former server on the server shelf and its control service config.")
try:
    settings = json.loads(pathlib.Path(config).read_bytes())
    identity, admin_hash = settings["fleet_id"], settings["admin_token_sha256"]
except (OSError, ValueError, KeyError, TypeError):
    fail("Cannot verify the previous controller's fleet identity.")
if not isinstance(admin_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", admin_hash):
    fail("Cannot identify the previous controller's service credential.")
if hmac.compare_digest(admin_hash, replacement_token_hash):
    fail("The previous address now reaches the replacement control service; nothing was retired.")
if identity != fleet:
    fail("The previous controller now belongs to another fleet.")
stage = pending(fragments[0][0]).parent
if os.path.lexists(stage) and (stage.is_symlink() or not stage.is_dir() or stage.stat().st_uid != os.geteuid()
                               or stage.stat().st_mode & 0o077):
    fail("Unrecognized staging directory; the previous ingress was left alone.")
for path, marker in fragments:
    other = pending(path)
    if os.path.lexists(path) and os.path.lexists(other):
        fail("The old nginx fragment changed while its removal was pending.")
    for candidate in (path, other):
        if not os.path.lexists(candidate):
            continue
        if candidate.is_symlink() or not candidate.is_file():
            fail("An unexpected file occupies an old Meridian nginx path.")
        contents = candidate.read_bytes()[:4096]
        if marker.encode() not in contents or b"# Managed by Meridian" not in contents:
            fail("An unrecognized nginx fragment occupies an old Meridian path.")
def run(*args):
    return subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
def system_state(unit_name, field):
    result = subprocess.run(["systemctl","show",unit_name,"-p",field,"--value"],
                            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
    if result.returncode or not result.stdout.strip():
        fail("Could not check the previous server's systemd state.")
    return result.stdout.strip()
nginx_active = system_state("nginx", "ActiveState")
if nginx_active not in ("active","reloading","inactive","failed"):
    fail("The previous nginx service is changing state; retry the restore.")
load_state = system_state(unit, "LoadState")
if load_state not in ("loaded", "masked", "not-found"):
    fail("The previous control service's unit state could not be verified.")
try:
    stage.mkdir(mode=0o700,exist_ok=True)
    for path, _ in fragments:
        if path.exists():
            path.rename(pending(path))
    staged = any(pending(path).exists() for path, _ in fragments)
    if staged:
        valid = run("nginx", "-t")
        active = nginx_active in ("active", "reloading")
        if not valid or (active and not run("systemctl", "reload", "nginx")):
            fail("The remaining nginx configuration could not be reloaded; the original fragments were restored.")
    if load_state == "loaded" and not run("systemctl", "disable", "--now", unit):
        fail("Could not disable the old control service.")
    if load_state == "masked" and not run("systemctl", "stop", unit):
        fail("Could not stop the masked previous control service.")
except BaseException:
    for path, _ in fragments:
        if pending(path).exists() and not os.path.lexists(path):
            pending(path).rename(path)
    if nginx_active in ("active", "reloading") and run("nginx", "-t"):
        run("systemctl", "reload", "nginx")
    raise
for path, _ in fragments:
    pending(path).unlink(missing_ok=True)
stage.rmdir()
print("verified")
"""


def detach_control_ingress(conn: ServerConnection, fleet_id: str, replacement_token_hash: str) -> None:
    command = shlex.join(
        ("python3", "-c", _DETACH, CONTROL_SERVICE_CONFIG, fleet_id, CONTROL_SERVICE_UNIT, replacement_token_hash)
    )
    result = conn.run(command, timeout=60, sensitive=True)
    if result.returncode or result.stdout.strip() != "verified":
        raise BackupError(
            "Could not retire the previous control server's ingress.",
            hint=(
                result.stderr.strip().splitlines()
                or ["Check ownership and nginx configuration, then repeat the restore."]
            )[-1][:200],
            category="user",
        )


def retire_bootstrap_certificate(conn: ServerConnection, hostname: str) -> None:
    remove_unused_certificate(conn, certificate_directory(hostname) + "/fullchain.pem", keep_challenge=False)
