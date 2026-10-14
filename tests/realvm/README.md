# Real-VM test harness

**Local-only.** Provisions actual cloud VMs (Hetzner today), runs Meridian
deploy + verification against them, then tears down. Complements the Docker
system-lab by covering things Docker cannot — real Let's Encrypt certs, real
UFW packet filtering, and real SSH hardening.

**Costs real money.** A full single-node run takes ~5-10 minutes, which at
Hetzner CX23 pricing is about €0.001 — essentially free for an occasional
validation, but it IS money leaving your account, and a bug (or forgotten
`--keep`) could leave a VM running. The harness has safety rails but no
substitute for paying attention.

> ⚠️ This harness **never runs in CI**. `HCLOUD_TOKEN` is never a GitHub
> Actions secret. Running cloud tests from CI is how budgets silently bleed;
> manual operation is intentional.

## Quick start

```bash
# 1. Install the harness-only Hetzner dependency
uv sync --extra dev --group realvm

# 2. Get a Hetzner API token
#    https://console.hetzner.cloud → Project → Security → API Tokens
#    Permission: Read & Write
export HCLOUD_TOKEN=<your-token>

# 3. Run the baseline topology
make real-lab
# (confirms cost, provisions, verifies, destroys)
```

That's it. On success you'll see `PASS: N  FAIL: 0` and the VM will be gone.
Set `MERIDIAN_TEST_SSH_KEY=/path/to/custom.pub` to override the default
`~/.ssh/id_ed25519.pub` upload.

## Topologies

Topology files live in `tests/realvm/topologies/*.yml`.

| Name | VMs | What it validates |
|---|---|---|
| `single` | 1 | Baseline — LE issuer, optional nmap, SSH, fleet status, client CRUD, declarative plan, subscription HTTP 200 |
| (future) `chain-2hop` | 2 | Domestic relay → foreign exit (tests chain correctness) |
| (future) `chain-3hop` | 3 | Full chain — client → ingress → middle → exit |
| (future) `dual-ip` | 1 | Node with 2 Primary IPs (ingress IP ≠ egress IP) |
| (future) `dual-stack-v6` | 1 | IPv4 ingress + IPv6 egress |

Run a non-default topology:

```bash
make real-lab TOPO=single
```

Only `single` has an automated verifier today. Definitions without one are
rejected before provider access, so they cannot create paid resources.

## Verification

Tier alpha runs by default with no user involvement.

- Certificate issuer reports Let's Encrypt
- Optional nmap scan finds no unexpected open ports in the tested set
- SSH password auth refused after hardened redeploy
- fail2ban active
- `meridian fleet status` reports the node connected
- Client add/list/remove round-trip via real panel API
- `meridian plan` exits 0 or 2 (converged or changes pending — both OK)
- Subscription URL returns HTTP 200

Domain/CDN and interactive mobile checks remain planned. Environment variables
do not enable those tiers until their orchestrator paths and verifiers exist.

## Exploratory labs

The one-shot run above destroys its VM when verification ends. For anything
longer (a multi-server journey, the wizard, page screenshots) bring up a lab
that stays until you tear it down:

```bash
make real-lab-up NODES="ctl:hel1 exit:fsn1:debian-13 relay:nbg1:ubuntu-22.04"
source ~/.cache/meridian-realvm/lab-<id>/env.sh   # ssh, scp, and meridian now target the lab
tests/realvm/journey.sh "$VM_CTL" "$VM_EXIT" "$VM_RELAY"
python -m tests.realvm.lab down <id>
```

Each lab directory holds a fresh SSH key (Hetzner rejects a public key another
run already uploaded), the VMs' pinned host keys, an `ssh_config` with
`ssh`/`scp` wrappers, and its own `MERIDIAN_HOME`. `env.sh` exports one
`VM_<NAME>` per VM.

- `journey.sh CONTROL [EXIT [RELAY]]` — deploy, `node add`, `relay add`,
  `client add`, fleet-wide `test`, `check`, a converged `apply`/`plan`,
  `doctor`, then removals that must leave no relay unit or certificate
  renewal behind. Exits 1 on any unexpected step result.
- `drive_wizard.py ADDRESS` / `drive_wizard.py --on-server IP` — answers the
  setup wizard like a newcomer (Enter everywhere except address and users);
  the transcript lands in the lab directory.
- `pwa_shots.py OUT URL [case ...]` — Playwright screenshots of a connection
  page across phone/desktop, light/dark, and languages.

Labs are labelled like harness runs, so orphan cleanup below finds them too.

## Orphan cleanup

If a run crashes or you `Ctrl+C` mid-provision, VMs may linger. The harness
tags every VM it creates with `meridian-realvm-test=1` plus a per-run
`meridian-fleet-id=<uuid>`. Find and destroy orphans:

```bash
# Show all harness-tagged VMs in your project
make real-lab-orphans

# Destroy them
make real-lab-down
```

`real-lab-down` destroys EVERY harness-tagged VM in the project — use with
care if you have multiple ongoing runs (unlikely in practice).

## What gets created (Hetzner)

Per run, for topology=single:

- 1 SSH key: `meridian-realvm-<fleet-id>` (public-key material from your
  `~/.ssh/id_ed25519.pub` or `MERIDIAN_TEST_SSH_KEY`)
- 1 server: `meridian-realvm-single-exit-1-<fleet-id>` (CX23, Ubuntu 24.04, Nuremberg)

All tagged with `meridian-realvm-test=1`, `meridian-fleet-id=<uuid>`,
`meridian-topology=single`.

Destroy path:
- VMs: deleted via API (instant)
- SSH key: best-effort removal (Hetzner reuses keys across projects — OK if
  left dangling, it's just a public key)

## Troubleshooting

**"HCLOUD_TOKEN is not set"** — generate a token at
https://console.hetzner.cloud → (your project) → Security → API Tokens. Give
it Read & Write permission. Export it in the shell session.

**"SSH on X.X.X.X:22 did not come up within 120s"** — VM booted but cloud-init
is still running. Rare, but can happen on first-ever image pull. Try again.

**"subscription URL returned HTTP 503"** — Remnawave panel didn't fully come
up. CX23 is the minimum viable size; if testing on smaller (not recommended),
bump to CX33.

**"certificate issuer does not mention Let's Encrypt"** after several rapid runs —
you've likely tripped LE's IP-cert rate limit (~5 shortlived issuances per
/32 IPv4 per 7 days) because Hetzner re-assigned the same Primary IPv4 from
its pool. `acme.sh` falls back to self-signed, the assertion fails. Fix:
destroy the fleet and re-run — a fresh VM usually gets a different IP.

**Stuck with orphan VMs after a crash** — `make real-lab-orphans` to list,
`make real-lab-down` to destroy.

## Why not Terraform / Pulumi?

Short answer: an external IaC tool would add a binary and a state file to a
harness that only needs to create and destroy a few VMs. Per-cloud SDK +
abstract `CloudProvider` class gives us:

- `uv sync --group realvm` — one dependency
- No state file (label-based discovery — crashed run leaves tagged orphans
  that are easy to find)
- Full access to provider-specific features (Hetzner Primary IPs, /64 IPv6
  subnets)
- Under 1 second per API call vs ~5s through a Pulumi state round-trip

The `CloudProvider` abstract class lives at
`tests/realvm/providers/base.py`; Hetzner impl at
`tests/realvm/providers/hetzner.py`. Adding a new provider is one file and
one entry in the `realvm` dependency group.
