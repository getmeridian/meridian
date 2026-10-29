# core - Meridian API contracts

## Design decisions
- **No CLI globals** - core never imports Typer, Rich, console, config, or command modules (tested); CLI and future UI clients are adapters. Core constants live in `core/defaults.py`.
- **Envelope first** - JSON clients get one stable `meridian.output/v1` shape; command data lives under `data`.
- **Events are a public set** - long-running flows report typed JSONL-ready events; add a name to `core.events` before emitting it.
- **Workflows are renderable data** - wizard-style interactions expose typed fields and sections; CLI and UI decide how to render them.
- **Command metadata is schema-neutral** - `command_catalog.COMMAND_OUTPUTS` maps every public command path to its JSON output (schemas, outcomes, stability) or `None`; argv, flags, and help come from the Typer tree via `meridian.cli_catalog`, and nothing here imports schema registration.
- **Pydantic at API boundaries** - request, result, event, error, and service contracts validate, serialize, and export JSON Schema from Pydantic v2 models.
- **Validate early, plan purely** - adapters validate typed request models at the boundary; deploy mode, ports, and paths are computed before any SSH or panel I/O.
- **Topology roles compose** - a server may hold several roles (relay and exit); ordered routes decide where traffic exits.
- **Verification is four-state** - passed, failed, warning, skipped; skipped evidence is inconclusive, never a pass.
- **Cooperative cancellation** - `core/cancellation.py` owns the token protocol, `OperationCancellation`, and `OperationCancelledError`; workers poll only at safe points.
- **Agent protocol is a contract, kept apart** - `meridian.agent/v1` (`agent_types`, `agent_desired`, `agent_protocol`, `agent_http`; `agent_signing` signs and verifies documents under the service certificate's `key_id`, and node proofs) is the internal service-to-node-agent wire format, consumed by the Rust agent; `schema_agent.AGENT_SCHEMAS` exports it to `contracts/meridian/v1/agent/`, never into `core.schema` (so `meridian api schemas` and Studio never list it). Docstrings are its spec; every field is always sent, no floats, agent-sent messages carry no address (tested); extend it only behind a negotiated capability. `version_skew.py` holds the CLI/service/agent compatibility rules.
- **Narrow panel interfaces** - services depend on protocols such as `FleetPanelClient` and `ClientPanelClient`, not the concrete Remnawave client.
- **Client limits** - `client_limits.py` parses binary traffic sizes (bounded by SQLite int64), UTC expiry/reset periods, and device caps (1–100/default) into `ClientLimitChange`. Explicit null clears expiry or uses the fleet cap; omitted fields preserve state. Device results compose IDs with full records whose nullable metadata fields are required, so private IDs never gain metadata during parsing.
- **Fleet document** - `fleet_document.py` owns the `meridian.fleet/v1` schema, server-ref mapping, and `intent_changes()`, the one description of how two intents differ; it reports a reorder rather than ever returning no change for unequal intents.
- **Privacy and usage contracts** — control.models owns settings, audit, and UTC day totals; command envelopes compose those same models. ClientShowResult carries optional seven-day usage, not a second history shape.
- **Two names per person** - `AccessIntent.users` are Remnawave usernames (Latin only); `display_names` keeps the typed name when it differs and is omitted when empty. `assign_access_usernames()` is the one place that maps typed names to usernames (known first, Latin names claim themselves, the rest transliterate via anyascii); look people up with `find_user()`.

## What's done well
- Delivery refresh intervals use one integer contract (1–168 hours); the six-hour default is omitted from new intent and settings fields to preserve existing serialization.
- No barrel re-exports in `__init__.py`; consumers import specific submodules.
- Shared serializers keep JSON output stable and recurse through core models.
- Fleet, client, node, and relay commands build typed, redacted results before any envelope or rendering.
- Reporter primitives let provision, apply, and deploy emit typed events without choosing a renderer.

## Pitfalls
- Fleet results carry live reachability, fresh typed runtime evidence, and convergence separately; a live poll never makes an old health report available.
- Never emit raw SSH commands, panel tokens, private keys, database URLs, or subscription secrets.
- `redact_string()` is the only secret masker (SSH debug logs included); teach it a new secret form instead of adding a local regex.
- Generated JSON Schemas are public data; redact values without destroying schema `properties`.
- SSH passwords and private keys never enter public server contracts; Engine handles them through short-lived secret channels.
- Keep human wording in adapters; core summaries are short API metadata.
- Wrap Pydantic `ValidationError` before rendering; raw model errors are too noisy for operators.
- Use `WithJsonSchema` for custom validators that need public schema constraints without replacing readable runtime errors.
- Keep topology IDs lowercase and bounded, and validate access usernames against the pinned panel contract before compilation.
- Command catalogs list only completed JSON outcomes: an interruption exits 130 without an envelope, while a cooperative cancel is a completed `cancelled` outcome with exit 5.
- Routing domains are suffixes: gateway rules and exit overrides validate with `validate_domain_suffix_value()` (bare zones like `ru`, `рф` → `xn--p1ai`); SNI and certificate names keep `validate_hostname_value()`, which needs two labels.
