"""Real-VM test harness entry point.

Parses a topology YAML, provisions VMs via a CloudProvider, runs verification,
tears down. Always tears down via `try/finally` even on crash — orphan cleanup
is a separate command (`make real-lab-orphans`).

Local-only. Never imported from CI workflows.

Exit codes of `up`: 0 passed, 1 findings, 2 refused or setup error, 75 inconclusive (a Let's
Encrypt rate limit stopped issuance, so the run proved nothing either way).

Usage:
    python -m tests.realvm.orchestrator up <topology_name>
    python -m tests.realvm.orchestrator down           # destroys all harness-tagged VMs
    python -m tests.realvm.orchestrator orphans        # lists orphan VMs (doesn't destroy)
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn, TextIO

import yaml

from tests.realvm.providers.base import CloudProvider, ProviderError, VMInstance, VMSpec
from tests.realvm.upgrade_support import redact_output

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
    ),
    "domain": (
        "panel_certificate_names_domain",
        "ip_certificate_without_sni",
        "acme_entries_use_separate_files",
        "connection_test_ip_paths",
        "health_check_ip_paths",
        "plan_converged_ip_paths",
        "exit_paths_moved_to_domain",
        "panel_certificate_survives_move",
        "connection_test_domain_paths",
        "health_check_domain_paths",
        "plan_converged_domain_paths",
        "ip_certificate_retired",
    ),
    "upgrade": (
        "published_release_deployed",
        "limited_client_created",
        "subscriptions_captured",
        "checkout_applied",
        "control_schema_current",
        "plan_converged",
        "subscriptions_unchanged",
        "client_metadata_preserved",
        "agent_version_current",
        "connection_test_passes",
    ),
}
# Node roles each topology must declare, in order.
TOPOLOGY_ROLES = {"single": ["exit"], "domain": ["exit"], "upgrade": ["exit"]}
# Verifier script per topology, under verify/.
VERIFIERS = {"single": "single.sh", "domain": "domain.sh", "upgrade": "upgrade.sh"}
# A verifier that reached no verdict (a Let's Encrypt rate limit); see tests/realvm/domain_support.py.
INCONCLUSIVE_EXIT = 75


def _print(message: str = "", *, file: TextIO | None = None) -> None:
    print(redact_output(message, known=(os.environ.get("HCLOUD_TOKEN", ""),)), file=file, flush=True)


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
        from tests.realvm.providers.hetzner import HetznerProvider

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

    _print()
    _print(f"  Topology:   {topology.name}")
    _print(f"  Provider:   {topology.provider}")
    _print(f"  Region:     {topology.region}")
    _print(f"  VM count:   {n_vms}  ({topology.size}, {topology.image})")
    _print(f"  Estimated:  {pretty} for {hours} hour(s)")
    _print("  Safety cap: 5 VMs (override with MERIDIAN_REALVM_FORCE=1)")
    _print()

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
    verifier = VERIFIERS.get(topology.name)
    if not verifier or not (Path(__file__).parent / "verify" / verifier).is_file():
        _fail(f"Topology {topology.name!r} has no verifier script; refusing to provision.")
    roles = [str(node.get("role", "")) for node in topology.nodes]
    if roles != TOPOLOGY_ROLES[topology.name]:
        _fail(f"Topology {topology.name!r} must define exactly one exit node; refusing to provision.")
    if set(topology.verify) != {"tier_alpha"}:
        _fail(
            f"Topology {topology.name!r} may declare only the implemented tier_alpha verifier; refusing to provision."
        )
    configured_checks = tuple(topology.verify.get("tier_alpha", []))
    if configured_checks != expected_checks:
        _fail(
            f"Topology {topology.name!r} verification manifest does not match the implemented verifier; "
            "refusing to provision."
        )


def require_agent_build() -> None:
    """Refuse before provisioning when this checkout has no node agent build to install."""
    from meridian.control_artifacts import require_agent_build as check
    from meridian.core.errors import MeridianError

    try:
        check()
    except MeridianError as exc:
        _fail(
            f"{exc} {exc.hint}\nA checkout deploys the agent it builds: set MERIDIAN_AGENT_BINARY or run `make agent`."
        )


# --------------------------------------------------------------------------
# Provision + destroy
# --------------------------------------------------------------------------


def provision(provider: CloudProvider, topology: Topology, fleet_id: str) -> list[VMInstance]:
    ssh_path, ssh_pub = _read_ssh_public_key()
    key_name = f"meridian-realvm-{fleet_id[:8]}"
    _print(f"  → Uploading SSH key {key_name} ...")
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
        _print(f"  → Provisioning {vm_name} ...")
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
            _print(f"    ✗ Provision failed: {e}")
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
        _print(f"    ✓ {instance.name} [{instance.public_ipv4}]")
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
    _print(f"  → Destroying fleet {fleet_id[:8]} ...")
    # Find all VMs with this fleet id
    vms = provider.list_vms({HARNESS_LABEL: "1", FLEET_ID_LABEL: fleet_id})
    ssh_key_ids = {vm.labels.get("meridian-ssh-key-id", "") for vm in vms}
    ssh_key_ids.discard("")

    for vm in vms:
        try:
            provider.destroy_vm(vm.id)
            _print(f"    ✓ destroyed {vm.name}")
        except Exception as e:
            _print(f"    ✗ could not destroy {vm.name}: {e}")

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
        _print("  No harness-tagged VMs found.")
        return
    _print(f"  Found {len(vms)} harness-tagged VM(s):")
    for vm in vms:
        fleet = vm.labels.get(FLEET_ID_LABEL, "?")
        _print(f"    - {vm.name}  [{vm.public_ipv4}]  fleet={fleet[:8]}")
    if not execute:
        _print("\n  Run with --execute to actually destroy them.")
        return
    for vm in vms:
        try:
            provider.destroy_vm(vm.id)
            _print(f"    ✓ destroyed {vm.name}")
        except Exception as e:
            _print(f"    ✗ could not destroy {vm.name}: {e}")


# --------------------------------------------------------------------------
# Verify — tier α (port of controller-run.sh key checks)
# --------------------------------------------------------------------------


def _verification_workspace(fleet_id: str) -> Path:
    workspace = Path(tempfile.mkdtemp(prefix=f"meridian-realvm-{fleet_id[:8]}-"))
    try:
        (workspace / "home").mkdir(mode=0o700)
        (workspace / "fleet-id").write_text(fleet_id + "\n")
    except BaseException:
        shutil.rmtree(workspace)
        raise
    return workspace


def _kept_workspace(workspace: Path) -> None:
    _print(f"  Kept fleet home: {workspace / 'home'}")
    if (workspace / "env.sh").exists():
        _print(f"  source {shlex.quote(str(workspace / 'env.sh'))}")
    _print(
        f'  uv run python -m tests.realvm.orchestrator down --fleet "$(cat {shlex.quote(str(workspace / "fleet-id"))})"'
    )


def run_verification(
    topology: Topology,
    instances: list[VMInstance],
    *,
    upgrade_from: str = "",
    keep: bool = False,
    workspace: Path | None = None,
) -> int:
    """Run tier α verification on the provisioned fleet. Returns exit code
    (0=all passed, non-zero=failures)."""
    # Single-VM topologies verify against instance[0]; multi-node ones need their own wiring.
    if topology.name not in VERIFIERS:
        _print(f"  ✗ Verification for {topology.name} is not implemented; refusing to report success.")
        return 2

    ip = instances[0].public_ipv4
    if not ip:
        _fail("VM has no public IPv4 — cannot verify.")

    _print(f"\n  Running tier α verification against {ip} ...\n")

    # Invoke the shell verify script — keeps Meridian CLI invocation logic
    # in shell (matches systemlab/controller-run.sh which developers know).
    verify_sh = Path(__file__).parent / "verify" / VERIFIERS[topology.name]
    if not verify_sh.exists():
        _print(f"  ✗ verify script not found: {verify_sh}")
        return 2

    # SSH ControlPath must live outside synced checkouts. A kept VM needs its local home too.
    fleet_id = instances[0].labels.get(FLEET_ID_LABEL, "unknown")
    owns_workspace = workspace is None
    workspace = workspace or _verification_workspace(fleet_id)
    meridian_home = workspace / "home"
    try:
        env = {
            **os.environ,
            "TARGET_IP": ip,
            "MERIDIAN_FLEET_ID": fleet_id,
            "MERIDIAN_HOME": str(meridian_home),
            "MERIDIAN_DISABLE_UPDATE_CHECK": "1",
        }
        if upgrade_from:
            env["REALVM_UPGRADE_FROM"] = upgrade_from
        with subprocess.Popen(
            ["bash", str(verify_sh)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        ) as process:
            assert process.stdout is not None
            for line in process.stdout:
                _print(line.rstrip("\n"))
            code = process.wait()
        if code == INCONCLUSIVE_EXIT:
            _print(
                f"\n  ? Inconclusive: {topology.name} reached no verdict (see above); this is not a product failure."
            )
        return code
    finally:
        if owns_workspace:
            if keep:
                _kept_workspace(workspace)
            else:
                shutil.rmtree(workspace)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def _fail(msg: str) -> NoReturn:
    _print(f"\n  ✗ {msg}\n", file=sys.stderr)
    sys.exit(2)


def cmd_up(args: argparse.Namespace) -> int:
    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        _fail("Real-VM provisioning is local-only and cannot run in CI.")
    topology = load_topology(args.topology)
    require_supported_topology(topology)
    require_agent_build()
    upgrade_from = ""
    if topology.name == "upgrade":
        from tests.realvm.upgrade_verifier import VerificationError, preflight_upgrade

        try:
            upgrade_from, _agent_version = preflight_upgrade(Path(__file__).parents[2], os.environ)
        except VerificationError as exc:
            _print(f"  {'Inconclusive' if exc.exit_code == INCONCLUSIVE_EXIT else 'Refused'}: {exc}")
            return exc.exit_code
    provider = make_provider(topology.provider)
    confirm_cost(provider, topology, hours=0.5)

    fleet_id = str(uuid.uuid4())
    _print(f"\n  Fleet prefix: {fleet_id[:8]}\n")

    instances: list[VMInstance] = []
    rc = 0
    workspace = _verification_workspace(fleet_id) if topology.name == "upgrade" else None
    previous_key = os.environ.get("MERIDIAN_TEST_SSH_KEY")
    try:
        if workspace is not None and not previous_key:
            key = workspace / "id_ed25519"
            try:
                subprocess.run(
                    ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"realvm-{fleet_id[:8]}", "-f", str(key)],
                    check=True,
                    capture_output=True,
                    stdin=subprocess.DEVNULL,
                    timeout=30,
                )
            except (OSError, subprocess.SubprocessError):
                _fail("Could not create the upgrade run's SSH key. Check that ssh-keygen is installed.")
            os.environ["MERIDIAN_TEST_SSH_KEY"] = f"{key}.pub"
        instances = provision(provider, topology, fleet_id)
        rc = run_verification(topology, instances, upgrade_from=upgrade_from, keep=args.keep, workspace=workspace)
    finally:
        if previous_key is None:
            os.environ.pop("MERIDIAN_TEST_SSH_KEY", None)
        else:
            os.environ["MERIDIAN_TEST_SSH_KEY"] = previous_key
        try:
            if args.keep:
                if workspace is not None:
                    _kept_workspace(workspace)
                _print(f"\n  --keep set; leaving VMs alive (fleet prefix {fleet_id[:8]}).")
                _print("  Use the printed cleanup command, or locate this fleet with `make real-lab-orphans`.\n")
            else:
                destroy_fleet(provider, fleet_id)
        finally:
            if workspace is not None and not args.keep:
                shutil.rmtree(workspace)

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
