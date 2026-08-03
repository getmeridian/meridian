# core - Meridian API contracts

## Design decisions
- **Core has no CLI globals** - no Typer, Rich, prompt, process exit, or console mode dependencies. CLI and future UI clients are adapters over core contracts.
- **Envelope first** - JSON clients get one stable `meridian.output/v1` result shape; command-specific data lives under `data`.
- **Events are JSONL-ready** - long-running flows should report progress as typed events, not ad hoc log lines.
- **Event names are a public set** - add event names in `core.events` before emitting them so schemas and Studio timelines stay typed.
- **Workflows are renderable data** - wizard-style interactions expose typed fields/sections; CLI and UI decide how to render them.
- **Contract export is direct** - scripts generate public schemas/catalogs from core imports, never by shelling out to the CLI.
- **Command metadata is schema-neutral** - command outcomes and bindings never import schema registration; schema embedding depends on them in one direction.
- **Pydantic at API boundaries** - public request/result/event/error/service contracts validate, serialize, and export JSON Schema from Pydantic v2 models.
- **Deploy planning is pure** - mode, ports, reusable paths, and request validation are computed before adapters perform SSH or panel I/O.
- **Input models fail early** - CLI and Engine adapters should validate typed core request models at the boundary, then pass trusted objects inward.
- **Server onboarding is UX-shaped** - collect titles, server IPs, SSH user/port, and stable references before mapping into storage or deploy requests.
- **Topology uses capabilities plus policy** - a server may be both relay and exit; routing rules decide where traffic exits.
- **Deploy process API is first-class** - `deploy` has a command contract, a typed output envelope, request-file input, dry-run plan output, and JSONL progress events for UI clients.
- **Remote execution is transport-neutral** - core workflows depend on executor contracts; SSH and future daemon transports live in adapters.
- **Verification is four-state** - probe and test share typed passed, failed, warning, and skipped checks; skipped evidence is inconclusive, never a pass.
- **Panel interfaces are narrow** — services depend on service-specific protocols such as `FleetPanelClient` and `ClientPanelClient`, not the concrete Remnawave implementation.

## What's done well
- No barrel re-exports in `__init__.py` — all consumers import from specific submodules, keeping the dependency graph explicit.
- Shared serializers keep JSON output stable and recursively handle core Pydantic models.
- Redaction is centralized so expanding JSON/API surfaces does not multiply secret-leak risk.
- Fleet inventory is built as a redacted result object before any CLI rendering happens.
- Client list/show use the same service/result/envelope pattern as fleet reads.
- Advertised client mutations and node/relay lists build typed results before command envelopes validate their wire shape.
- Reporter primitives let provision/apply/deploy flows emit typed events without choosing a renderer.

## Pitfalls
- Do not import command modules, `meridian.console`, `meridian.config`, Typer, or Rich here. Core constants live in `core/defaults.py`.
- Do not emit raw SSH commands, panel tokens, private keys, JWTs, database URLs, or subscription secrets.
- Keep human wording in adapters; core summaries are short API metadata, not terminal copy.
- Wrap Pydantic `ValidationError` before rendering; raw model errors are too noisy for non-expert operators.
- Use `WithJsonSchema` for custom validators that need public schema constraints without replacing readable runtime errors.
- Generated JSON Schemas are public data; redact values without destroying schema `properties`.
- Do not put SSH passwords or private keys into public server contracts; Engine must handle them through short-lived secret channels.
- Keep topology IDs lowercase and bounded, and validate access usernames against the pinned panel contract before compilation.
- Plan/apply envelopes use distinct typed legacy and compiled results; preserve legacy schema names and add a separate command-data union.
- Command catalogs list only completed JSON outcomes; process interruption exits 130 without promising a terminal envelope.
