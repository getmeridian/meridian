# scripts — Repository automation

## Design decisions

**Direct imports for contracts** — contract generation imports `meridian.core` schemas/services directly and never shells out to `meridian`.

**Deterministic output** — scripts that write tracked artifacts must have a `--check` mode for CI drift detection.

## What's done well

- `export_contracts.py` keeps schemas, command catalog, workflow manifests, event types, and fixtures generated from one core source.

## Pitfalls

- Do not hide CLI prompts or process exits inside scripts that generate API contracts.
- Keep generated fixture clocks, operation IDs, and durations stable.
