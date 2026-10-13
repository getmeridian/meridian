# tests/realvm/providers — Cloud provider abstraction

Python-first VM lifecycle for the real-VM harness. No Terraform, no Pulumi.

## Design decisions

- **Per-cloud SDK, not an external IaC tool** — no `terraform` / `pulumi` binary or state file; harness-only, never part of the published package.
- **Label-based discovery, no state file** — every resource is tagged `meridian-realvm-test=1` + `meridian-fleet-id=<uuid>`. Lost context → `list_vms(labels=…)` finds orphans. No fragile state round-trip.
- **Deferred SDK import** — `HetznerProvider.__init__` validates the token BEFORE importing `hcloud`, so missing optional dependency raises a typed `ProviderError`, not `ImportError`. Unit-testable without the SDK installed.
- **SDKs live in the `realvm` dependency group** — `uv sync --group realvm` pulls `hcloud>=2.0`; `make real-lab*` targets pass `--group realvm`. Add a provider's SDK to that group.

## What's done well

- **Clean `CloudProvider` abstract** — `create_vm / destroy_vm / get_vm / list_vms / upload_ssh_key / delete_ssh_key / estimate_cost`. Small surface, stable.
- **`VMSpec.extras` escape hatch** — provider-specific knobs (Hetzner placement groups, DO backups) pass through without bloating the abstract.
- **Cost estimator** — every provider can expose an hourly-rate dict for `estimate_cost`; harness prints a figure before provisioning. Safety rail against surprise bills.

## Pitfalls

- **Never let label schema drift** between provision and destroy. If `create_vm` writes `meridian-fleet-id`, `destroy_fleet` MUST read the same key.
- **Primary IP ≠ Floating IP on Hetzner** — Primary IPs are the recommended modern API. Floating IPs are legacy. Code should create Primary IPs for multi-IP scenarios.
- **hcloud Server type IDs drift over time** — Hetzner deprecates old sizes (CX22 in 2025). Catch deprecation errors at provider level, surface actionable message.
- **SSH key pinning** — `upload_ssh_key` is idempotent on name + fingerprint match; different material with the same name raises.

## Links

- Abstract + dataclasses: `base.py`
- Hetzner impl: `hetzner.py`
- Tests: `tests/test_infra_providers.py`
- Harness consumer: `tests/realvm/orchestrator.py`
