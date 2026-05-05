# core - Meridian API contracts

## Design decisions
- **Core has no CLI globals** - no Typer, Rich, prompt, process exit, or console mode dependencies. CLI and future UI clients are adapters over core contracts.
- **Envelope first** - JSON clients get one stable `meridian.output/v1` result shape; command-specific data lives under `data`.
- **Events are JSONL-ready** - long-running flows should report progress as typed events, not ad hoc log lines.
- **Event names are a public set** - add event names in `core.events` before emitting them so schemas and Studio timelines stay typed.
- **Workflows are renderable data** - wizard-style interactions expose typed fields/sections; CLI and UI decide how to render them.
- **Contract export is direct** - scripts generate public schemas/catalogs from core imports, never by shelling out to the CLI.
- **Pydantic at API boundaries** - public request/result/event/error/service contracts validate, serialize, and export JSON Schema from Pydantic v2 models.
- **Deploy planning is pure** - mode, ports, reusable paths, and request validation are computed before adapters perform SSH or panel I/O.
- **Input models fail early** - CLI and Engine adapters should validate typed core request models at the boundary, then pass trusted objects inward.
- **Deploy process API is first-class** - `deploy` has a command contract, a typed output envelope, request-file input, dry-run plan output, and JSONL progress events for UI clients.
- **Remote execution is transport-neutral** - core workflows depend on executor contracts; SSH and future daemon transports live in adapters.

## What's done well
- Shared serializers keep JSON output stable and recursively handle core Pydantic models.
- Redaction is centralized so expanding JSON/API surfaces does not multiply secret-leak risk.
- Fleet inventory is built as a redacted result object before any CLI rendering happens.
- Client list/show use the same service/result/envelope pattern as fleet reads.
- Reporter primitives let provision/apply/deploy flows emit typed events without choosing a renderer.

## Pitfalls
- Do not import command modules, `meridian.console`, Typer, or Rich here.
- Do not emit raw SSH commands, panel tokens, private keys, JWTs, database URLs, or subscription secrets.
- Keep human wording in adapters; core summaries are short API metadata, not terminal copy.
- Wrap Pydantic `ValidationError` before rendering; raw model errors are too noisy for non-expert operators.
- Use `WithJsonSchema` for custom validators that need public schema constraints without replacing readable runtime errors.
- Generated JSON Schemas are public data; redact values without destroying schema `properties`.
