# Contributing to Meridian

Thanks for your interest! Here's how to help.

## Reporting Issues

- **Bug?** Use the [bug report template](https://github.com/getmeridian/meridian/issues/new?template=bug_report.yml) — run `meridian doctor` first to collect info
- **Connection not working?** Use the [connection issue template](https://github.com/getmeridian/meridian/issues/new?template=connection_issue.yml) — run `meridian test` and `meridian preflight` first
- **Feature idea?** Use the [feature request template](https://github.com/getmeridian/meridian/issues/new?template=feature_request.yml)
- **Security vulnerability?** See [SECURITY.md](SECURITY.md) — do NOT open a public issue

## Development Setup

```bash
git clone https://github.com/getmeridian/meridian.git && cd meridian

# Install the CLI in editable mode with all dev dependencies (uses uv sync --extra dev)
make install

# Install pre-push hook (runs 11 fast checks before every push)
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
- `provision/` — Provisioner engine: idempotent steps with structured tracing and error output

## Pull Requests

1. Fork the repo and create a branch from `main`
2. Make your changes — keep them focused and minimal
3. Ensure CI passes: `make ci` (runs all checks locally)
4. Test on a real server if possible — CI can't catch deployment issues
5. Open a PR with a clear description of what and why

## Architecture Notes

See [CLAUDE.md](CLAUDE.md) for detailed architecture, implicit dependencies, and conventions. Key points:

- **Shell values use `shlex.quote()`** — never interpolate unsanitized values into shell commands
- **PWA assets must stay in sync** (`templates/pwa/` and the application catalog)
- **nginx config** uses `/etc/nginx/stream.d/meridian.conf` plus `/etc/nginx/conf.d/meridian-http.conf`
- **Provisioner steps** return `StepResult` (ok/changed/skipped/failed) for structured tracing


## Testing

There's no way to fully test without a real server. The CI pipeline validates:
- Python tests (`pytest`) — cluster state, server profiles, CLI, protocols, update logic
- Python lint (`ruff`) — style and import checks
- Type checking (`mypy`) — static type analysis
- Jinja2 template rendering with mock variables
- Shell script syntax (`install.sh`)

For deployment testing, use a cheap VPS and run the full uninstall → install cycle.
