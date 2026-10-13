"""Exploratory multi-VM lab: named VMs that stay up until you tear them down.

The one-shot `orchestrator up` verifies a topology and destroys it; a lab is
for journeys (`journey.sh`), wizard runs (`drive_wizard.py`), and page
screenshots (`pwa_shots.py`). Each lab gets its own directory under
`~/.cache/meridian-realvm/` (`REALVM_LABS`) holding a fresh SSH key (Hetzner rejects a public key
that another run already uploaded), pinned host keys, an ssh_config, ssh/scp
wrappers, and an isolated MERIDIAN_HOME. VMs carry the harness labels, so
`make real-lab-orphans` still finds anything left behind.

Usage:
    python -m tests.realvm.lab up ctl:hel1 exit:fsn1:debian-13 relay:nbg1:ubuntu-22.04
    source ~/.cache/meridian-realvm/lab-<id>/env.sh  # ssh/scp/meridian now target the lab
    python -m tests.realvm.lab list
    python -m tests.realvm.lab down <id>           # destroys VMs and the lab directory
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from meridian.infra.providers import VMInstance
from tests.realvm.orchestrator import (
    FLEET_ID_LABEL,
    HARNESS_LABEL,
    Topology,
    confirm_cost,
    destroy_fleet,
    make_provider,
    provision,
)

# Outside the checkout: the lab holds private keys, and some synced folders refuse the Unix
# socket that Meridian keeps under MERIDIAN_HOME for SSH multiplexing.
LABS = Path(os.environ.get("REALVM_LABS", Path.home() / ".cache" / "meridian-realvm"))
DEFAULT_SIZE = "cx23"
DEFAULT_IMAGE = "ubuntu-24.04"


def lab_dir(fleet_id: str) -> Path:
    return LABS / f"lab-{fleet_id[:8]}"


def _parse_node(item: str) -> tuple[str, str, str]:
    parts = item.split(":")
    if len(parts) not in (2, 3) or not all(parts):
        raise SystemExit(f"  ✗ Expected NAME:REGION[:IMAGE], got {item!r}")
    return parts[0], parts[1], parts[2] if len(parts) == 3 else DEFAULT_IMAGE


def _pin_host_keys(ip: str, known_hosts: Path, timeout_sec: int = 120) -> None:
    """Record the VM's host keys; sshd may answer TCP before it serves keys."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        scan = subprocess.run(["ssh-keyscan", "-T", "5", ip], capture_output=True, text=True)
        if scan.stdout.strip():
            with known_hosts.open("a") as handle:
                handle.write(scan.stdout)
            return
        time.sleep(3)
    raise SystemExit(f"  ✗ No SSH host keys from {ip} within {timeout_sec}s")


def _write_lab_files(root: Path, vms: dict[str, str]) -> None:
    key = root / "id_ed25519"
    config = root / "ssh_config"
    config.write_text(
        "Host *\n"
        f"  IdentityFile {key}\n"
        "  IdentitiesOnly yes\n"
        "  IdentityAgent none\n"
        f"  UserKnownHostsFile {root / 'known_hosts'}\n"
        "  StrictHostKeyChecking yes\n"
        "  ServerAliveInterval 15\n"
    )
    bin_dir = root / "bin"
    bin_dir.mkdir(exist_ok=True)
    known_hosts = shlex.quote(str(root / "known_hosts"))
    wrappers = {
        "ssh": f'exec /usr/bin/ssh -F {shlex.quote(str(config))} "$@"\n',
        "scp": f'exec /usr/bin/scp -F {shlex.quote(str(config))} "$@"\n',
        # Meridian checks host keys with `ssh-keygen -F`; point lookups at the lab's pinned keys.
        "ssh-keygen": (
            f'case " $* " in *" -F "*|*" -R "*) exec /usr/bin/ssh-keygen -f {known_hosts} "$@";; esac\n'
            'exec /usr/bin/ssh-keygen "$@"\n'
        ),
    }
    for name, body in wrappers.items():
        path = bin_dir / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)
    exports = {
        "PATH": f"{bin_dir}:$PATH",
        "MERIDIAN_HOME": str(root / "home"),
        "MERIDIAN_DISABLE_UPDATE_CHECK": "1",
        "REALVM_LAB": str(root),
        "PYTHONUNBUFFERED": "1",
    }
    exports.update({f"VM_{name.upper().replace('-', '_')}": ip for name, ip in vms.items()})
    lines = [f'export {name}="{value}"' for name, value in exports.items()]
    (root / "env.sh").write_text("# source me: ssh, scp, and meridian target this lab\n" + "\n".join(lines) + "\n")
    (root / "home").mkdir(exist_ok=True)


def cmd_up(args: argparse.Namespace) -> int:
    nodes = [_parse_node(item) for item in args.nodes]
    if len(nodes) > 5:
        raise SystemExit("  ✗ A lab holds at most 5 VMs.")
    provider = make_provider(args.provider)
    first_region, first_image = nodes[0][1], nodes[0][2]
    everyone = [{"name": name} for name, _, _ in nodes]
    confirm_cost(provider, Topology("lab", args.provider, first_region, args.size, first_image, everyone), hours=2)

    fleet_id = str(uuid.uuid4())
    root = lab_dir(fleet_id)
    root.mkdir(parents=True)
    key = root / "id_ed25519"
    comment = f"realvm-{fleet_id[:8]}"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(key)], check=True)
    os.environ["MERIDIAN_TEST_SSH_KEY"] = f"{key}.pub"

    instances: list[VMInstance] = []
    try:
        for name, region, image in nodes:
            topology = Topology("lab", args.provider, region, args.size, image, [{"name": name}])
            instances += provision(provider, topology, fleet_id)
        vms = {name: instance.public_ipv4 or "" for (name, _, _), instance in zip(nodes, instances, strict=True)}
        for ip in vms.values():
            _pin_host_keys(ip, root / "known_hosts")
        _write_lab_files(root, vms)
    except BaseException:
        print("  ✗ Lab setup failed; destroying what was created.")
        destroy_fleet(provider, fleet_id)
        shutil.rmtree(root, ignore_errors=True)
        raise
    (root / "lab.json").write_text(json.dumps({"fleet": fleet_id, "vms": vms}, indent=2) + "\n")
    print(f"\n  Lab {fleet_id[:8]} is up: {', '.join(f'{n}={ip}' for n, ip in vms.items())}")
    print(f"    source {root / 'env.sh'}")
    print(f"    python -m tests.realvm.lab down {fleet_id[:8]}   # when done\n")
    return 0


def _resolve_lab(prefix: str) -> tuple[Path, str]:
    matches = [path for path in LABS.glob(f"lab-{prefix[:8]}*") if (path / "lab.json").exists()]
    if len(matches) != 1:
        raise SystemExit(f"  ✗ No single lab matches {prefix!r}; see `python -m tests.realvm.lab list`.")
    return matches[0], json.loads((matches[0] / "lab.json").read_text())["fleet"]


def cmd_down(args: argparse.Namespace) -> int:
    root, fleet_id = _resolve_lab(args.lab)
    destroy_fleet(make_provider(args.provider), fleet_id)
    shutil.rmtree(root)
    print(f"  ✓ Lab {fleet_id[:8]} destroyed")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    for path in sorted(LABS.glob("lab-*/lab.json")):
        lab = json.loads(path.read_text())
        print(f"  local  {lab['fleet'][:8]}  " + " ".join(f"{n}={ip}" for n, ip in lab["vms"].items()))
    for vm in make_provider(args.provider).list_vms({HARNESS_LABEL: "1"}):
        print(f"  cloud  {vm.labels.get(FLEET_ID_LABEL, '?')[:8]}  {vm.name} {vm.public_ipv4}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.realvm.lab", description=__doc__.splitlines()[0])
    parser.add_argument("--provider", default="hetzner")
    sub = parser.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("up", help="Create named VMs: NAME:REGION[:IMAGE] ...")
    up.add_argument("nodes", nargs="+")
    up.add_argument("--size", default=DEFAULT_SIZE)
    up.set_defaults(func=cmd_up)
    down = sub.add_parser("down", help="Destroy a lab's VMs and its directory")
    down.add_argument("lab", help="Lab id (first characters are enough)")
    down.set_defaults(func=cmd_down)
    sub.add_parser("list", help="Local labs and harness VMs in the cloud").set_defaults(func=cmd_list)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
