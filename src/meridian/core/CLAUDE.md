# core - Meridian API contracts

## Design decisions
- `OwnerContactSettings` shares the optional reviewed contact across delivery intent, compiler payloads, and pages; empty defaults omit the field, text is one plain line up to 120 characters, and HTTPS/TG/mailto links canonicalize to bounded ASCII without a lookup.
- `person_metadata.py` owns plain-text notes (500 Unicode characters, newlines allowed) and one uppercase A–Z/digit/underscore tag (1–16); empty strings clear, omitted edits preserve, and creation intent never contains them.
- Reviewed delivery command schemas register through `schema_delivery.py`, using the catalog's shared envelope base and failure model so names and wire fields stay stable.
- **Server metadata** — `server_allowances.py` owns reviewed allowances, bounded provider labels, payment dates, and nullable measurement contracts; `server_calendar.py` preserves UTC reset/payment anchors through short months and leap years, and empty topology metadata is omitted.
- **Server schema registration** — `schema_servers.py` owns the server entries in the public catalog; `schema.py` composes that mapping with the other contract families.
- **Creation-only account settings** — `UserCreationSettings` is shared by service creation and `AccessIntent.creation_settings`; an empty mapping is omitted so existing user payloads and plans keep their hashes.
- **Attention predicates** — `attention.py` derives typed person/node reasons from aggregate evidence, with bounded day/percentage thresholds and no stored alert flags; client list composes the same reasons when `--attention` is requested.
- **Local lifecycle facts** — `fleet_events.py` is the durable event contract for integrations: monotonic ID, UTC time, kind, actor, subject, and a closed detail model per kind; command progress keeps its separate `events.py` contract.
- **Traffic evidence** — `core/traffic.py` is the shared aggregate evidence contract: first accepted use and last reporting node per person, plus active people and collection/receipt times in each node's latest report; unknown historical evidence remains null.
- **Connection-page settings** — `connection_pages.py` owns branding and exit-country fields shared with service settings; empty defaults are omitted so existing JSON and old databases retain their defaults.
- **No CLI globals** - core never imports Typer, Rich, console, config, or command modules (tested); CLI and future UI clients are adapters. Core constants live in `core/defaults.py`.
- **Envelope first** - JSON clients get one stable `meridian.output/v1` shape; command data lives under `data`. `schema_envelopes.py` builds standard command envelopes from the catalog's base and failure model, preserving names, fields, and discriminator mappings; `schema.py` keeps a thin delegation.
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
- Client routing has one `ru-split | full` type; new intent and settings fields omit `ru-split`, preserving existing serialized plans and payloads.
- Delivery refresh intervals use one integer contract (1–168 hours); the six-hour default is omitted from new intent and settings fields to preserve existing serialization.
- Each command kind is its own hello capability; successful `reset_inbound` results disclose `inbound_reset` or `core_restart` through the nullable `effect` field, without carrying a user identifier.
- No barrel re-exports in `__init__.py`; consumers import specific submodules.
- Shared serializers keep JSON output stable and recurse through core models.
- Fleet, client, node, and relay commands build typed, redacted results before any envelope or rendering.
- Reporter primitives let provision, apply, and deploy emit typed events without choosing a renderer.

## Pitfalls
- Treat individual connection URI and SVG QR fields as complete credentials; client show reveals only their exact producing paths, never passive output or neighboring fields.
- JSON reveal options accept exact dotted paths, including list indices; preserve authorized private note/tag fields without revealing neighboring URLs or credentials.
- Provider labels allow Persian ZWNJ/ZWJ joiners while rejecting line breaks, bidi controls, and other control characters.
- Preserve server metadata through setup drafts and fleet name/ref mapping; metadata names only declared physical servers and never moves automatically with an exit.
- Fleet diffs compare creation settings for each person in access-user order; adding, editing, or removing that mapping is not a fleet reorder.
- Timestamp validates calendar dates and clock times after its wire-pattern check; preserve the input string so fractional precision and generated schemas stay unchanged.
- A fresh healthy runtime can still be awaiting convergence; fleet status counts that separately and degrades until in_sync, while stale reports remain unavailable evidence.
- Client detail carries lifetime traffic and the current period's start through the access adapter; resetting period use never erases the lifetime figure.
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
