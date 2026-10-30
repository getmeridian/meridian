---
title: Contributing
description: Development setup, PR guidelines, and testing approach.
---

## Reporting issues

- **Bug?** Use the [bug report template](https://github.com/getmeridian/meridian/issues/new?template=bug_report.yml) — run `meridian doctor` first
- **Connection issue?** Use the [connection issue template](https://github.com/getmeridian/meridian/issues/new?template=connection_issue.yml) — run `meridian test` and `meridian check` first
- **Feature idea?** Use the [feature request template](https://github.com/getmeridian/meridian/issues/new?template=feature_request.yml)
- **Security vulnerability?** See [Security](/en/security/) — do NOT open a public issue

## Development setup

```bash
git clone https://github.com/getmeridian/meridian.git && cd meridian

# Install CLI in editable mode with dev dependencies
make install

# Install the pre-push hook (fast checks before every push)
make hooks

# Run full CI locally
make ci

# Individual checks:
make test              # pytest
make lint              # ruff check
make format-check      # ruff format --check
make typecheck         # mypy
make templates         # Jinja2 template validation
```

## Project structure

The CLI is a Python package (`src/meridian/`) distributed via PyPI as `meridian-vpn`.

Key modules:
- `cli.py` — Typer app, subcommand registration
- `commands/` — one module per subcommand
- `cluster.py` + `cluster_persistence.py` — typed `cluster.yml` state and persistence
- `servers.py` — `ServerRegistry` for known servers
- `setup/` — `meridian setup` wizard state and the shared runtime behind deploy, setup, and apply
- `compiler/` — turns the topology into a reviewed, hash-identified list of resources (no I/O)
- `reconciler/` — observes and applies those resources with checkpoints
- `provision/` — server preparation steps (packages, hardening, the control service, Realm)
- `control/` — the control service (`meridian serve`): users, subscriptions, signed node documents, backups, and the web panel
- `backup/` — snapshots, device keys, and `meridian restore`

The node agent is a Rust crate in `agent/` at the repository root; its builds ship inside the Python package.

## Pull requests

1. Fork the repo and create a branch from `main`
2. Make focused, minimal changes
3. Ensure CI passes: `make ci`
4. Test on a real server if possible
5. Open a PR with a clear description

## Key conventions

- **Shell values use `shlex.quote()`** — never interpolate unsanitized values
- **PWA assets must stay in sync** (`templates/pwa/` and the application catalog)
- **nginx config** is written per resource as `meridian-v4-*.conf` files under `/etc/nginx/stream.d/` and `/etc/nginx/sites-available/`
- **Provisioner steps** return `StepResult` (ok/changed/skipped/failed)

## Testing

CI validates: Python tests, ruff lint, mypy types, template rendering, and shell script syntax. For full deployment testing, run `make system-lab` (deploys a multi-node fleet in Docker containers and checks real traffic) or test on a real VPS (`make real-lab`).
