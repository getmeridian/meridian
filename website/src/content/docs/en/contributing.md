---
title: Contributing
description: Development setup, PR guidelines, and testing approach.
order: 12
section: reference
---

## Reporting issues

- **Bug?** Use the [bug report template](https://github.com/getmeridian/meridian/issues/new?template=bug_report.yml) — run `meridian doctor` first
- **Connection issue?** Use the [connection issue template](https://github.com/getmeridian/meridian/issues/new?template=connection_issue.yml) — run `meridian test` and `meridian preflight` first
- **Feature idea?** Use the [feature request template](https://github.com/getmeridian/meridian/issues/new?template=feature_request.yml)
- **Security vulnerability?** See [Security](/docs/en/security/) — do NOT open a public issue

## Development setup

```bash
git clone https://github.com/getmeridian/meridian.git && cd meridian

# Install CLI in editable mode with dev dependencies
make install

# Install pre-push hook (11 fast checks before every push)
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
- `provision/` — idempotent step pipeline

## Pull requests

1. Fork the repo and create a branch from `main`
2. Make focused, minimal changes
3. Ensure CI passes: `make ci`
4. Test on a real server if possible
5. Open a PR with a clear description

## Key conventions

- **Shell values use `shlex.quote()`** — never interpolate unsanitized values
- **PWA assets must stay in sync** (`templates/pwa/` and the application catalog)
- **nginx config** uses `/etc/nginx/stream.d/meridian.conf` plus `/etc/nginx/conf.d/meridian-http.conf`
- **Provisioner steps** return `StepResult` (ok/changed/skipped/failed)

## Testing

CI validates: Python tests, ruff lint, mypy types, template rendering, and shell script syntax. Full deployment testing requires a real VPS.
