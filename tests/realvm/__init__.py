"""Real-VM integration test harness.

Local-only — NEVER runs in CI. Provisions real VMs on a cloud provider
(default: Hetzner), runs Meridian deploy + verification, tears down.

Usage:
    export HCLOUD_TOKEN=...
    make real-lab TOPO=single
    make real-lab-down  # manual cleanup if needed

See tests/realvm/README.md for full setup.
"""
