# contracts — Generated meridian-core artifacts

## Design decisions

**Generated, checked in** — `scripts/export_contracts.py` is the source of truth. Do not hand-edit files below `contracts/meridian/`.

**Core imports only** — contract export imports `meridian.core` services and schemas directly; it must not spawn `meridian` or import command modules.

**Redacted fixtures** — golden fixtures are safe passive artifacts. Operator-visible handoff details remain available through explicit UI/CLI result views.

## What's done well

- Contracts include schemas, command catalog, workflow catalog, event types, and deploy fixtures in one deterministic tree.
- `--check` gives CI a cheap drift gate before Studio or Engine clients depend on stale shapes.

## Pitfalls

- Do not make generated TypeScript or Studio forms the source of truth.
- Keep fixture clocks, operation IDs, and durations fixed so diffs are meaningful.
