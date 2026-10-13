# Contributing to Meridian

## Reporting Issues

- **Bug?** Use the [bug report template](https://github.com/getmeridian/meridian/issues/new?template=bug_report.yml) — run `meridian doctor` first to collect info
- **Connection not working?** Use the [connection issue template](https://github.com/getmeridian/meridian/issues/new?template=connection_issue.yml) — run `meridian test` and `meridian check` first
- **Feature idea?** Use the [feature request template](https://github.com/getmeridian/meridian/issues/new?template=feature_request.yml)
- **Security vulnerability?** See [SECURITY.md](SECURITY.md) — do NOT open a public issue

## Development Setup

```bash
git clone https://github.com/getmeridian/meridian.git && cd meridian

# Install the CLI in editable mode with all dev dependencies (uses uv sync --extra dev)
make install

# Install the pre-push hook (fast checks before every push)
make hooks

# Run full CI locally (lint + format + test + templates)
make ci

# Or run individual checks:
make test              # Run Python tests (pytest)
make lint              # Run ruff linter
make format-check      # Check formatting
make typecheck         # Run mypy type checker
make templates         # Validate Jinja2 template rendering
```

## Project Structure

The CLI is a Python package (`src/meridian/`) distributed via PyPI as `meridian-vpn`. Server provisioning is handled by the pure-Python provisioner in `src/meridian/provision/`.

Key modules:
- `cli.py` — Typer app, subcommand registration
- `commands/` — One module per subcommand (setup, client, check, etc.)
- `cluster.py` + `cluster_persistence.py` — typed `cluster.yml` state and persistence
- `servers.py` — `ServerRegistry` for the known servers index
- `setup/` — `meridian setup` wizard state and the shared runtime behind deploy, setup, and apply
- `compiler/` — turns the topology into a reviewed, hash-identified list of resources (no I/O)
- `reconciler/` — observes and applies those resources with checkpoints
- `provision/` — server preparation steps (packages, hardening, Docker, Realm)

## Pull Requests

1. Fork the repo and create a branch from `main`
2. Make your changes — keep them focused and minimal
3. Ensure CI passes: `make ci` (runs all checks locally)
4. For deployment changes, run `make system-lab` or test on a real server
5. Open a PR with a clear description of what and why

## Architecture Notes

See [CLAUDE.md](CLAUDE.md) for detailed architecture, implicit dependencies, and conventions. Key points:

- **Shell values use `shlex.quote()`** — never interpolate unsanitized values into shell commands
- **PWA assets must stay in sync** (`templates/pwa/` and the application catalog)
- **nginx config** is written per resource as `meridian-v4-*.conf` files under `/etc/nginx/stream.d/` and `/etc/nginx/sites-available/`
- **Provisioner steps** return `StepResult` (ok/changed/skipped/failed) for structured tracing


## Testing

The CI pipeline validates:
- Python tests (`pytest`) — cluster state, server profiles, CLI, protocols, update logic
- Python lint (`ruff`) — style and import checks
- Type checking (`mypy`) — static type analysis
- Jinja2 template rendering with mock variables
- Shell script syntax (`install.sh`)

For deployment testing:
- `make system-lab` deploys a multi-node fleet (exits, relay hops, a routing gateway) in Docker containers and checks real traffic through it
- `make real-lab` provisions a Hetzner VM, deploys, verifies, and destroys it (local only, needs `HCLOUD_TOKEN`; see `tests/realvm/README.md`)
- On your own VPS, run `meridian teardown` and then `meridian deploy` again to test a clean install
