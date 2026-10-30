# contracts — Generated meridian-core artifacts

## Design decisions
- `admin/openapi.json` is the English OpenAPI 3.1 contract generated from the control route catalog and Pydantic models; it uses the package version on export and the installed service version at runtime, with explicit credential and scope extensions.
- Calendar-renewal schemas describe the one/three-month preview and exact UTC result; previews never mutate a user.
- Individual connection and format URL schemas compose client-show's optional view; generated artifacts describe credentials without embedding them.
- Owner contact request/result schemas and inherited presentation fields export their text bounds and allowed URI schemes; default empty contact stays omitted from intent and settings payloads.
- Administrative person schemas describe private notes and tags with their bounds; generated schemas contain no private values, and lower-scope API records keep these fields as empty strings.
- **Server billing contracts** — server list/set envelopes, metadata, service settings, and nullable period usage are exported from `core/schema_servers.py`; fleet and attention contracts compose the same server evidence.

**Generated, checked in** — `scripts/export_contracts.py` is the source of truth. Do not hand-edit files below `contracts/meridian/`.

**Core imports plus the CLI tree** — contract export imports `meridian.core` services and schemas, and `meridian.cli_catalog` walks the in-process Typer app for `commands.json`; it never spawns `meridian`, imports command modules, or hits prompts and process exits.

**Agent protocol apart** — `agent/` holds the internal `meridian.agent/v1` service-to-agent contract (`agent/schemas/`, `agent/http.json`) for the Rust node agent; it stays out of `schemas/`, so Studio's generator and `meridian api schemas` never see it.

**Redacted fixtures** — golden fixtures are safe passive artifacts. Operator-visible handoff details remain available through explicit UI/CLI result views.

## What's done well

- Contracts include schemas, command catalog, workflow catalog, event types, and deploy fixtures in one deterministic tree.
- `--check` gives CI a cheap drift gate before Studio or Engine clients depend on stale shapes.

## Pitfalls

- Do not make generated TypeScript or Studio forms the source of truth.
- Keep fixture clocks, operation IDs, and durations fixed so diffs are meaningful.
- Never write Python-version-dependent text into a contract: `HTTPStatus.phrase` changed in 3.13 (413 "Request Entity Too Large" → "Content Too Large"), so the OpenAPI generator pins RFC 9110 phrases.
