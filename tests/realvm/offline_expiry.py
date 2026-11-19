"""Check a disposable single exit: live VPN traffic through a signed offline expiry.

Needs an isolated MERIDIAN_HOME, its enrolled SSH key, local OpenSSH/OpenBSD nc and
the named lab's REALVM_LAB manifest and pinned native Xray (MERIDIAN_TEST_XRAY
or the lab's home/bin). The only
network destination is the fixture VM: an SSH echo through each saved subscription.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import secrets
import selectors
import shlex
import shutil
import subprocess
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from meridian.cluster import ClusterConfig
from meridian.control.client import ControlClient
from meridian.control.models import UserUpdate
from meridian.control.times import format_timestamp
from tests.realvm.client_probe import _free_port, _listening, _xray


class DrillError(Exception):
    pass


def check(value: object, message: str) -> None:
    if not value:
        raise DrillError(message)


def stop(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def vpn(stack: ExitStack, tmp: Path, panel: Any, token: str, binary: str, protocol: str) -> int:
    reply = httpx.get(panel.subscription_url(token), headers={"User-Agent": "Happ/1.0"}, timeout=15, trust_env=False)
    check(reply.is_success, "Could not save the synthetic person's subscription.")
    configs = reply.json()
    out = next(
        (
            o
            for c in (configs if isinstance(configs, list) else [configs])
            for o in c.get("outbounds", [])
            if (
                o.get("streamSettings", {}).get("security") == "reality"
                if protocol == "reality"
                else o.get("protocol") == "hysteria"
            )
        ),
        None,
    )
    check(out, "The requested native VPN transport was not in the subscription.")
    port = _free_port()
    path = tmp / f"client-{port}.json"
    path.write_text(
        json.dumps(
            {
                "log": {"loglevel": "none"},
                "inbounds": [
                    {"listen": "127.0.0.1", "port": port, "protocol": "socks", "settings": {"auth": "noauth"}}
                ],
                "outbounds": [out],
            }
        )
    )
    proc = subprocess.Popen([binary, "run", "-c", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    stack.callback(stop, proc)
    check(_listening(port, proc), "The saved subscription did not start locally.")
    return port


def roundtrip(proc: subprocess.Popen, deadline: float = 8) -> bool:
    assert proc.stdin and proc.stdout
    value = secrets.token_hex(12).encode() + b"\n"
    try:
        proc.stdin.write(value)
        proc.stdin.flush()
        with selectors.DefaultSelector() as select:
            select.register(proc.stdout, selectors.EVENT_READ)
            return bool(select.select(deadline)) and proc.stdout.readline() == value
    except (BrokenPipeError, OSError):
        return False


def run(args: argparse.Namespace) -> None:
    ip = str(ipaddress.IPv4Address(args.host))
    check(os.environ.get("REALVM_LAB"), "Source a named disposable lab's env.sh before running this drill.")
    lab = Path(os.environ["REALVM_LAB"]).resolve(strict=True)
    manifest = json.loads((lab / "lab.json").read_text())
    fleet = str(UUID(manifest["fleet"]))
    check(lab.name == f"lab-{fleet[:8]}", "The lab directory does not match its fleet manifest.")
    check(ip in manifest["vms"].values(), "The requested host is not in this named lab's VM manifest.")
    home = Path(os.environ["MERIDIAN_HOME"]).resolve(strict=True)
    check(home == lab / "home", "MERIDIAN_HOME must be this lab's isolated home.")
    key = lab / "id_ed25519"
    check(args.ssh_key.resolve(strict=True) == key, "The SSH key must be this lab's generated key.")
    public = (lab / "id_ed25519.pub").read_text().split()
    check(len(public) == 3 and public[2] == f"realvm-{fleet[:8]}", "The SSH identity does not name this lab.")
    lab_hosts = lab / "known_hosts"
    known = Path(args.known_hosts or os.environ.get("REALVM_KNOWN_HOSTS") or lab_hosts).resolve(strict=True)
    check(known == lab_hosts, "SSH host keys must come from this disposable lab.")
    check((home / "cluster.yml").is_file() and args.ssh_key.is_file(), "Missing explicit lab home or fixture SSH key.")
    panel = ClusterConfig.load(home / "cluster.yml").panel
    check(
        panel.server_ip == ip and urlsplit(panel.url).hostname == ip,
        "The IPv4 test panel must point to the named lab VM.",
    )
    check(shutil.which("nc"), "An OpenBSD-compatible nc is required for the native client.")
    binary = _xray()
    ssh = [
        "ssh",
        "-p",
        str(panel.ssh_port or 22),
        "-i",
        str(args.ssh_key),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "ControlMaster=no",
        "-o",
        "ControlPath=none",
        "-o",
        "ConnectTimeout=5",
    ]
    ssh.extend(["-o", f"UserKnownHostsFile={known}", "-o", "StrictHostKeyChecking=yes"])
    target = f"{args.ssh_user}@{ip}"

    def remote(command: str) -> str:
        proc = subprocess.run(
            [*ssh, target, command], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15
        )
        check(proc.returncode == 0, "A fixture SSH control command failed.")
        return proc.stdout.strip()

    def echo(stack: ExitStack, port: int) -> subprocess.Popen:
        proc = subprocess.Popen(
            [*ssh, "-o", f"ProxyCommand=nc -X 5 -x 127.0.0.1:{port} %h %p", target, "cat"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        stack.callback(stop, proc)
        return proc

    def probe(port: int) -> bool:
        with ExitStack() as stack:
            return roundtrip(echo(stack, port))

    check(
        remote("systemctl is-active ctld netcore netagent").splitlines() == ["active"] * 3,
        "Expected an active disposable control and exit.",
    )
    with tempfile.TemporaryDirectory() as private, ExitStack() as stack:
        tmp = Path(private)
        api = stack.enter_context(ControlClient(panel.url, panel.api_token, report_mutations=False))
        tag = secrets.token_hex(4)
        users = []
        for name in [f"expiry-{tag}", f"witness-{tag}"]:
            check(api.get_user(name) is None, "Synthetic name collision.")
            person = api.put_user(name)
            stack.callback(api.delete_user, name)
            users.append(person)
        a, b = [vpn(stack, tmp, panel, u.sub_token, binary, args.protocol) for u in users]
        live = echo(stack, a)
        check(roundtrip(live) and probe(b), "Both saved subscriptions must carry SSH data before the outage.")
        epoch = int(remote("date +%s")) + 35
        api.update_user(users[0].username, UserUpdate(expire_at=format_timestamp(epoch)))
        # Read only a boolean remotely. Neither the held document nor any person's credential leaves the node.
        script = (
            "import json; s=json.load(open('/var/lib/netagent/state.json')); h=s['held']; "
            "print(int(s['applied']['sha256']==h['sha256'] and any("
            f"u.get('user_id')=={users[0].user_id!r} and u.get('expires_unix')=={epoch} "
            "for r in h['resources'] for u in r.get('users',[]))))"
        )
        until = time.monotonic() + 18
        while remote(f"python3 -c {shlex.quote(script)}") != "1":
            check(time.monotonic() < until, "The exit never held the signed deadline.")
            time.sleep(0.5)
        initial = remote("systemctl show netcore -p InvocationID --value")
        check(initial, "Missing initial core instance.")
        timer = f"meridian-expiry-drill-{tag}"
        remote(f"systemd-run --quiet --unit={timer} --on-active=3m /usr/bin/systemctl start ctld")
        try:
            remote("systemctl stop ctld && ! systemctl is-active --quiet ctld")
            check(int(remote("date +%s")) < epoch, "Control stopped too late to prove offline expiry.")
            check(roundtrip(live), "The active transfer did not survive loss of control.")
            remaining = epoch - int(remote("date +%s"))
            time.sleep(max(0, remaining) + 2)
            check(roundtrip(live) and probe(b), "Expiry interrupted an established transfer or a different person.")
            check(not probe(a), "The expired person still opened a new working VPN connection.")
            check(not probe(a), "A repeated fresh connection passed the offline deadline.")
            check(remote("systemctl show netcore -p InvocationID --value") == initial, "The core restarted at expiry.")
            remote("systemctl restart netagent && ! systemctl is-active --quiet ctld")
            check(
                roundtrip(live) and probe(b) and not probe(a), "An offline agent restart changed the access decision."
            )
        finally:
            remote("systemctl start ctld")
            remote(f"systemctl stop {timer}.timer")
        until = time.monotonic() + 25
        while True:
            try:
                api.health()
                break
            except Exception:
                check(time.monotonic() < until, "The control service failed to return.")
                time.sleep(0.5)
        api.update_user(users[0].username, UserUpdate(expire_at=format_timestamp(epoch + 3600)))
        until = time.monotonic() + 75
        while not probe(a):
            check(time.monotonic() < until, "Renewed access never returned.")
            time.sleep(0.5)
        check(roundtrip(live) and probe(b), "Renewal interrupted another open transfer.")
        print(
            f"PASS: {args.protocol} offline expiry, original stream, unaffected person, agent restart, and renewal.",
            flush=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--ssh-user", default="root")
    parser.add_argument("--ssh-key", required=True, type=Path)
    parser.add_argument("--known-hosts", type=Path)
    parser.add_argument("--protocol", choices=("reality", "hysteria2"), default="reality")
    try:
        run(parser.parse_args())
    except DrillError as error:
        print("FAIL:", error, flush=True)
        return 1
    except Exception as error:
        # HTTP exception strings can contain private URLs.
        print("FAIL: fixture operation failed (" + type(error).__name__ + ").", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
