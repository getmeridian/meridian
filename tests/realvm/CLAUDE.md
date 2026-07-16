# tests/realvm — Real-VM integration test harness

Provisions real cloud VMs (Hetzner via `hcloud-python`), deploys Meridian
against them, verifies, tears down. **Local-only — NEVER runs in CI.**
Costs real money (~€0.01 per full single-topology run).

## Design decisions

- **Never in CI** — `HCLOUD_TOKEN` is not a GitHub secret; harness files live outside `.github/workflows/`; `pyproject.toml` `norecursedirs = ["realvm"]` keeps `pytest` from collecting. Runs only via `make real-lab` locally.
- **Topology per YAML file** — YAML declares provider / region / size / nodes, but `up` rejects any topology or verification manifest without an implemented verifier before provider access.
- **Executable claims only** — Tier α covers LE issuer, optional nmap, SSH, fail2ban, fleet, client CRUD, plan, and subscription HTTP; planned domain/interactive tiers stay out of manifests until implemented.
- **Per-cloud SDK, no Terraform/Pulumi** — see `src/meridian/infra/CLAUDE.md` for rationale.
- **MERIDIAN_HOME isolation per fleet** — orchestrator sets `MERIDIAN_HOME=<fleet-id>/` before invoking `meridian deploy`, so the harness's cluster.yml doesn't clobber the developer's real `~/.meridian/`.

## What's done well

- **Safety rails**: pre-provider verifier gate, `HCLOUD_TOKEN` gate, cost prompt, 5-VM cap, `try/finally` teardown, and label-based orphan sweep.
- **Live-validated on Hetzner** — topology `single` is the sole supported automated definition.
- **Unbuffered output** via `PYTHONUNBUFFERED=1` in Makefile targets — real-time progress visible during long deploys.

## Pitfalls

- **Never put `HCLOUD_TOKEN` into GitHub Actions** — running real-VM tests from CI leaks budget silently when a workflow misbehaves.
- **Always teardown in `finally`** — the orchestrator does it; verify scripts must not hold the VM open on failure (except `--keep` explicitly).
- **Hetzner server types deprecate** — CX22 was deprecated in 2025; harness defaults to `cx23`. Refresh topology YAMLs and `providers/hetzner.py::_HOURLY_EUR` when upstream sunsets a generation.
- **Rich console ANSI in captured output** — `meridian fleet status` renders colors; prefer `meridian --json fleet status` in verify scripts for stable field access.
- **JSON command output is enveloped** — read command fields below `data`; there are no legacy top-level payloads in v4.
- **Never return success for an unsupported topology** — add its verifier and manifest contract before allowing provisioning.
- **Let's Encrypt IP-cert rate limit** — repeated bare-IP runs can trigger self-signed fallback and fail the issuer assertion; recreate the VM for a fresh IP.

## Links

- Orchestrator: `orchestrator.py`
- Topology specs: `topologies/<name>.yml`
- Tier α verify: `verify/single.sh`
- README (user-facing): `README.md`
