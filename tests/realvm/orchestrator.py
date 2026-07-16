"""Real-VM test harness entry point.

Parses a topology YAML, provisions VMs via a CloudProvider, runs verification,
tears down. Always tears down via `try/finally` even on crash — orphan cleanup
is a separate command (`make real-lab-orphans`).

Local-only. Never imported from CI workflows.

Usage:
    python -m tests.realvm.orchestrator up <topology_name>
    python -m tests.realvm.orchestrator down           # destroys all harness-tagged VMs
    python -m tests.realvm.orchestrator orphans        # lists orphan VMs (doesn't destroy)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from meridian.infra.providers import CloudProvider, ProviderError, VMInstance, VMSpec

HARNESS_LABEL = "meridian-realvm-test"  # marker label — always "1" on harness VMs
FLEET_ID_LABEL = "meridian-fleet-id"  # per-run uuid, groups VMs of one run
DEFAULT_SSH_KEY = Path.home() / ".ssh" / "id_ed25519.pub"
SUPPORTED_TOPOLOGIES = {
    "single": (
        "le_certificate_issuer",
        "ufw_external_ports_if_nmap_available",
        "sshd_pubkey_only",
        "fail2ban_active",
        "fleet_status_connected",
        "client_add_list_remove_roundtrip",
        "declarative_plan",
        "subscription_url_200",
    )
}


@dataclass
class Topology:
    """Parsed topology.yml."""

    name: str
    provider: str
    region: str
    size: str
    image: str
    nodes: list[dict[str, Any]]
    verify: dict[str, list[str]] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Provider factory
# --------------------------------------------------------------------------


def make_provider(name: str) -> CloudProvider:
    """Import the matching CloudProvider impl. Raises if SDK missing or token absent."""
    if name == "hetzner":
        token = os.environ.get("HCLOUD_TOKEN", "")
        if not token:
            _fail(
                "HCLOUD_TOKEN is not set.\n"
                "Get a read-write project token from https://console.hetzner.cloud/projects\n"
                "then: export HCLOUD_TOKEN=<your-token>"
            )
        from meridian.infra.providers.hetzner import HetznerProvider

        return HetznerProvider(token=token)
    _fail(f"Unknown provider: {name!r}. Supported: hetzner")
    raise AssertionError  # unreachable; _fail() sys.exits


# --------------------------------------------------------------------------
# Safety gates
# --------------------------------------------------------------------------


def confirm_cost(provider: CloudProvider, topology: Topology, hours: float = 0.5) -> None:
    """Estimate + prompt. Skipped if MERIDIAN_REALVM_YES=1 (for scripted runs)."""
    n_vms = len(topology.nodes)
    spec_for_est = VMSpec(name="dummy", image=topology.image, size=topology.size, region=topology.region)
    per_vm = provider.estimate_cost(spec_for_est, hours=hours)
    total = per_vm * n_vms if per_vm is not None else None
    pretty = f"≈ €{total:.4f}" if total is not None else "unknown (provider has no pricing API)"

    print()
    print(f"  Topology:   {topology.name}")
    print(f"  Provider:   {topology.provider}")
    print(f"  Region:     {topology.region}")
    print(f"  VM count:   {n_vms}  ({topology.size}, {topology.image})")
    print(f"  Estimated:  {pretty} for {hours} hour(s)")
    print("  Safety cap: 5 VMs (override with MERIDIAN_REALVM_FORCE=1)")
    print()

    if n_vms > 5 and os.environ.get("MERIDIAN_REALVM_FORCE", "0") != "1":
        _fail(f"Topology wants {n_vms} VMs — above the safety cap.\nSet MERIDIAN_REALVM_FORCE=1 to proceed.")

    if os.environ.get("MERIDIAN_REALVM_YES", "0") == "1":
        return
    answer = input("  Proceed? [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        _fail("Aborted by user.")


def _read_ssh_public_key() -> tuple[str, str]:
    """Return (key_path, public_key_contents). Falls back to ed25519 default."""
    override = os.environ.get("MERIDIAN_TEST_SSH_KEY", "")
    path = Path(override) if override else DEFAULT_SSH_KEY
    if not path.exists():
        _fail(
            f"SSH public key not found at {path}.\n"
            f"Either:\n"
            f"  1. Generate one: ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519\n"
            f"  2. Set MERIDIAN_TEST_SSH_KEY=/path/to/your/key.pub"
        )
    return str(path), path.read_text().strip()


# --------------------------------------------------------------------------
# Topology loading
# --------------------------------------------------------------------------


def load_topology(name: str) -> Topology:
    topologies_dir = Path(__file__).parent / "topologies"
    path = topologies_dir / f"{name}.yml"
    if not path.exists():
        available = sorted(p.stem for p in topologies_dir.glob("*.yml"))
        _fail(f"Topology {name!r} not found. Available: {', '.join(available) or '(none)'}")
    data = yaml.safe_load(path.read_text())
    if data.get("name") != name:
        _fail(f"Topology file {path.name!r} must declare name: {name}")
    return Topology(
        name=data["name"],
        provider=data["provider"],
        region=data["region"],
        size=data["size"],
        image=data["image"],
        nodes=data.get("nodes", []),
        verify=data.get("verify", {}),
    )


def require_supported_topology(topology: Topology) -> None:
    """Reject unimplemented or drifted definitions before provider access."""
    expected_checks = SUPPORTED_TOPOLOGIES.get(topology.name)
    if expected_checks is None:
        _fail(
            f"Topology {topology.name!r} has no automated verifier; refusing to provision. "
            f"Supported: {', '.join(sorted(SUPPORTED_TOPOLOGIES))}"
        )
    roles = [str(node.get("role", "")) for node in topology.nodes]
    if topology.name == "single" and roles != ["exit"]:
        _fail("Topology 'single' must define exactly one exit node; refusing to provision.")
    if set(topology.verify) != {"tier_alpha"}:
        _fail("Topology 'single' may declare only the implemented tier_alpha verifier; refusing to provision.")
    configured_checks = tuple(topology.verify.get("tier_alpha", []))
    if configured_checks != expected_checks:
        _fail("Topology 'single' verification manifest does not match the implemented verifier; refusing to provision.")


# --------------------------------------------------------------------------
# Provision + destroy
# --------------------------------------------------------------------------


def provision(provider: CloudProvider, topology: Topology, fleet_id: str) -> list[VMInstance]:
    ssh_path, ssh_pub = _read_ssh_public_key()
    key_name = f"meridian-realvm-{fleet_id[:8]}"
    print(f"  → Uploading SSH key {key_name} ...")
    ssh_key_id = provider.upload_ssh_key(name=key_name, public_key=ssh_pub)

    labels = {
        HARNESS_LABEL: "1",
        FLEET_ID_LABEL: fleet_id,
        "meridian-topology": topology.name,
        # Stash ssh-key-id on the server label so destroy_fleet (which refetches
        # from the cloud API) can find and clean it up without a separate lookup.
        "meridian-ssh-key-id": ssh_key_id,
    }

    instances: list[VMInstance] = []
    for i, node_spec in enumerate(topology.nodes):
        vm_name = f"meridian-realvm-{topology.name}-{node_spec.get('name', f'n{i}')}-{fleet_id[:8]}"
        print(f"  → Provisioning {vm_name} ...")
        spec = VMSpec(
            name=vm_name,
            image=topology.image,
            size=topology.size,
            region=topology.region,
            ssh_key_ids=[ssh_key_id],
            labels=labels,
        )
        try:
            instance = provider.create_vm(spec)
        except ProviderError as e:
            # On provision failure, teardown what we already have.
            print(f"    ✗ Provision failed: {e}")
            for done in instances:
                try:
                    provider.destroy_vm(done.id)
                except Exception:
                    pass
            try:
                provider.delete_ssh_key(ssh_key_id)
            except Exception:
                pass
            raise
        print(f"    ✓ {instance.name} [{instance.public_ipv4}]")
        instances.append(instance)

    # Wait for ssh to actually accept connections (cloud-init may still be
    # running). Simple port check with short timeout.
    for inst in instances:
        _wait_ssh(inst.public_ipv4 or "", port=22, timeout_sec=120)

    return instances


def _wait_ssh(ip: str, port: int, timeout_sec: int) -> None:
    """Block until SSH port accepts TCP connections or timeout."""
    if not ip:
        return
    import socket

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with socket.create_connection((ip, port), timeout=3):
                return
        except OSError:
            time.sleep(3)
    _fail(f"SSH on {ip}:{port} did not come up within {timeout_sec}s")


def destroy_fleet(provider: CloudProvider, fleet_id: str) -> None:
    """Destroy every VM with the given fleet-id label, plus its SSH key."""
    print(f"  → Destroying fleet {fleet_id[:8]} ...")
    # Find all VMs with this fleet id
    vms = provider.list_vms({HARNESS_LABEL: "1", FLEET_ID_LABEL: fleet_id})
    ssh_key_ids = {vm.labels.get("meridian-ssh-key-id", "") for vm in vms}
    ssh_key_ids.discard("")

    for vm in vms:
        try:
            provider.destroy_vm(vm.id)
            print(f"    ✓ destroyed {vm.name}")
        except Exception as e:
            print(f"    ✗ could not destroy {vm.name}: {e}")

    # SSH public keys carry no ongoing cost on Hetzner, but tidy anyway.
    # The key id is stashed in the VM label at provision time.
    for key_id in ssh_key_ids:
        try:
            provider.delete_ssh_key(key_id)
        except Exception:
            pass  # best-effort — leaving an orphan public key is harmless


def destroy_all_orphans(provider: CloudProvider, *, execute: bool = False) -> None:
    vms = provider.list_vms({HARNESS_LABEL: "1"})
    if not vms:
        print("  No harness-tagged VMs found.")
        return
    print(f"  Found {len(vms)} harness-tagged VM(s):")
    for vm in vms:
        fleet = vm.labels.get(FLEET_ID_LABEL, "?")
        print(f"    - {vm.name}  [{vm.public_ipv4}]  fleet={fleet[:8]}")
    if not execute:
        print("\n  Run with --execute to actually destroy them.")
        return
    for vm in vms:
        try:
            provider.destroy_vm(vm.id)
            print(f"    ✓ destroyed {vm.name}")
        except Exception as e:
            print(f"    ✗ could not destroy {vm.name}: {e}")


# --------------------------------------------------------------------------
# Verify — tier α (port of controller-run.sh key checks)
# --------------------------------------------------------------------------


def run_verification(topology: Topology, instances: list[VMInstance]) -> int:
    """Run tier α verification on the provisioned fleet. Returns exit code
    (0=all passed, non-zero=failures)."""
    # For single-node topology, verification runs against instance[0].
    # Multi-node topologies override this.
    if topology.name != "single":
        print(f"  ✗ Verification for {topology.name} is not implemented; refusing to report success.")
        return 2

    ip = instances[0].public_ipv4
    if not ip:
        _fail("VM has no public IPv4 — cannot verify.")

    print(f"\n  Running tier α verification against {ip} ...\n")

    # Invoke the shell verify script — keeps Meridian CLI invocation logic
    # in shell (matches systemlab/controller-run.sh which developers know).
    verify_sh = Path(__file__).parent / "verify" / "single.sh"
    if not verify_sh.exists():
        print(f"  ✗ verify script not found: {verify_sh}")
        return 2

    # Isolate Meridian's config dir per fleet — otherwise `meridian deploy`
    # refuses to run when the developer already has a cluster.yml configured
    # from unrelated work. Cleans up on destroy via rmtree.
    fleet_id = instances[0].labels.get(FLEET_ID_LABEL, "unknown")
    meridian_home = Path(__file__).parent / ".local" / f"fleet-{fleet_id[:8]}"
    meridian_home.mkdir(parents=True, exist_ok=True)

    env = {
        **os.environ,
        "TARGET_IP": ip,
        "MERIDIAN_FLEET_ID": fleet_id,
        "MERIDIAN_HOME": str(meridian_home),
    }
    result = subprocess.run(["bash", str(verify_sh)], env=env)
    return result.returncode


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def _fail(msg: str) -> None:
    print(f"\n  ✗ {msg}\n", file=sys.stderr)
    sys.exit(2)


def cmd_up(args: argparse.Namespace) -> int:
    topology = load_topology(args.topology)
    require_supported_topology(topology)
    provider = make_provider(topology.provider)
    confirm_cost(provider, topology, hours=0.5)

    fleet_id = str(uuid.uuid4())
    print(f"\n  Fleet id: {fleet_id}\n")

    instances: list[VMInstance] = []
    rc = 0
    try:
        instances = provision(provider, topology, fleet_id)
        rc = run_verification(topology, instances)
    finally:
        if args.keep:
            print("\n  --keep set; leaving VMs alive. Clean up with:")
            print(f"    python -m tests.realvm.orchestrator down --fleet {fleet_id}\n")
        else:
            destroy_fleet(provider, fleet_id)

    return rc


def cmd_down(args: argparse.Namespace) -> int:
    # Without --fleet, destroy ALL harness VMs in the account (orphans).
    provider = make_provider(args.provider)
    if args.fleet:
        destroy_fleet(provider, args.fleet)
    else:
        destroy_all_orphans(provider, execute=True)
    return 0


def cmd_orphans(args: argparse.Namespace) -> int:
    provider = make_provider(args.provider)
    destroy_all_orphans(provider, execute=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Real-VM test harness for Meridian (local-only, NEVER run in CI).",
        prog="python -m tests.realvm.orchestrator",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_up = sub.add_parser("up", help="Provision + verify + destroy")
    p_up.add_argument("topology", help="Topology name (from topologies/ dir)")
    p_up.add_argument("--keep", action="store_true", help="Don't auto-destroy after verify")
    p_up.set_defaults(func=cmd_up)

    p_down = sub.add_parser("down", help="Destroy harness-tagged VMs")
    p_down.add_argument("--fleet", help="Destroy only VMs of this fleet id")
    p_down.add_argument("--provider", default="hetzner", help="Provider to query")
    p_down.set_defaults(func=cmd_down)

    p_orph = sub.add_parser("orphans", help="List harness VMs in the account (no destroy)")
    p_orph.add_argument("--provider", default="hetzner")
    p_orph.set_defaults(func=cmd_orphans)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
