# scripts — Repository automation

## Design decisions

**Direct imports for contracts** — contract generation imports `meridian.core` schemas/services and `meridian.cli_catalog` (the in-process Typer tree) directly and never shells out to `meridian`.

**Deterministic output** — scripts that write tracked artifacts must have a `--check` mode for CI drift detection.

**Dead-code report** — `find_dead_code.py` (stdlib `ast`) builds a reference graph over `src/meridian`: imports resolve precisely through re-exports, attributes and production strings match members by name. Liveness spreads from production roots (src top level, `scripts/`, entry points, registration decorators), then from `tests/`; it reports `dead`, `test-only`, and `unused-param` as sorted warnings in `make ci` and CI, failing only under `--strict`.

**Conditional Studio packaging** — `hatch_build.py` bundles `website/dist` when present so clean source installs work; release/CI builds verify the generated Studio marker inside the wheel.

## What's done well

- `pin_image_digests.py` resolves every `*_IMAGE` constant in `meridian.config` to its multi-arch index digest, refuses indexes without linux/amd64 and linux/arm64, and prints paste-ready lines (network, never CI).
- `export_contracts.py` keeps schemas, command catalog, workflow manifests, event types, and fixtures generated from one core source.

## Pitfalls

- Do not hide CLI prompts or process exits inside scripts that generate API contracts.
- Pin the index (manifest-list) digest, never a per-platform manifest digest: arm64 servers would fail to pull an amd64-only pin.
- Keep generated fixture clocks, operation IDs, and durations stable.
- Allowlist only real false positives in `dead_code_allowlist.txt`, each with a `# reason`; delete genuine dead code (and the tests keeping it alive) instead. Teach the analyzer a recurring reference pattern rather than allowlisting every name it hides.
