# tests/realvm/providers — Cloud provider abstraction

Python-first VM lifecycle for the real-VM harness. No Terraform, no Pulumi.

## Design decisions

- **Per-cloud SDK, not an external IaC tool** — no `terraform` / `pulumi` binary; harness-only, never part of the published package.
- **Label-based discovery, no state file** — every resource is tagged `meridian-realvm-test=1` + `meridian-fleet-id=<uuid>`, so `list_vms(labels=…)` finds orphans after lost context.
- **Deferred SDK import** — `HetznerProvider.__init__` validates the token BEFORE importing `hcloud`, so missing optional dependency raises a typed `ProviderError`, not `ImportError`. Unit-testable without the SDK installed.
- **SDKs live in the `realvm` dependency group** — `uv sync --group realvm` pulls `hcloud>=2.0`; `make real-lab*` targets pass `--group realvm`. Add a provider's SDK to that group.

## What's done well

- **Small `CloudProvider` surface** — VM create/destroy/get/list, SSH key upload/delete, and `estimate_cost`; provider-specific knobs ride `VMSpec.extras` instead of widening it.
- **Cost before provisioning** — providers expose hourly rates so the harness prints an estimate before creating anything.

## Pitfalls

- **Never let label schema drift** between provision and destroy. If `create_vm` writes `meridian-fleet-id`, `destroy_fleet` MUST read the same key.
- **Use Hetzner Primary IPs, not legacy Floating IPs**, for multi-IP scenarios.
- **SSH key pinning** — `upload_ssh_key` is idempotent on name + fingerprint match; different material with the same name raises.

## Links

- Abstract + dataclasses: `base.py`
- Hetzner impl: `hetzner.py`
- Tests: `tests/test_infra_providers.py`
- Harness consumer: `tests/realvm/orchestrator.py`
