# core — Meridian API contracts

## Design decisions
- **No CLI globals** — core never imports Typer, Rich, console, config, or command modules (tested); CLI and future UI clients are adapters. Core constants live in `core/defaults.py`.
- **Envelope first** — JSON clients get one stable `meridian.output/v1` shape; command data lives under `data`. `schema_envelopes.py` builds standard command envelopes from the catalog's base and failure model, preserving names, fields, and discriminator mappings; `schema.py` keeps a thin delegation and composes the contract families, such as the server entries `schema_servers.py` owns and the reviewed delivery command schemas `schema_delivery.py` registers on the same envelope base and failure model.
- **Command metadata is schema-neutral** — `command_catalog.COMMAND_OUTPUTS` maps every public command path to its JSON output (schemas, outcomes, stability) or `None`; argv, flags, and help come from the Typer tree via `meridian.cli_catalog`, and nothing here imports schema registration.
- **Events are a public set** — long-running flows report typed JSONL-ready events; add a name to `core.events` before emitting it.
- **Workflows are renderable data** — wizard-style interactions expose typed fields and sections; CLI and UI decide how to render them.
- **Pydantic at API boundaries** — request, result, event, error, and service contracts validate, serialize, and export JSON Schema from Pydantic v2 models.
- **Validate early, plan purely** — adapters validate typed request models at the boundary; deploy mode, ports, and paths are computed before any SSH or panel I/O.
- **Topology roles compose** — a server may hold several roles (relay and exit); ordered routes decide where traffic exits.
- **Verification is four-state** — passed, failed, warning, skipped; skipped evidence is inconclusive, never a pass.
- **Cooperative cancellation** — `core/cancellation.py` owns the token protocol, `OperationCancellation`, and `OperationCancelledError`; workers poll only at safe points.
- **Agent protocol is a contract, kept apart** — `meridian.agent/v1` (`agent_types`, `agent_desired`, `agent_protocol`, `agent_http`; `agent_signing` signs and verifies documents under the service certificate's `key_id`, and node proofs) is the internal service-to-node-agent wire format, consumed by the Rust agent; `schema_agent.AGENT_SCHEMAS` exports it to `contracts/meridian/v1/agent/`, never into `core.schema` (so `meridian api schemas` and Studio never list it). Docstrings are its spec; every field is always sent, no floats, agent-sent messages carry no address (tested); extend it only behind a negotiated capability. `version_skew.py` holds the CLI/service/agent compatibility rules.
- **Narrow panel interfaces** — services depend on protocols such as `FleetPanelClient` and `ClientPanelClient`, not the concrete Remnawave client.
- **Two names per person** — `AccessIntent.users` are Remnawave usernames (Latin only); `display_names` keeps the typed name when it differs and is omitted when empty. `assign_access_usernames()` is the one place that maps typed names to usernames (known first, Latin names claim themselves, the rest transliterate via anyascii); look people up with `find_user()`.
- **Fleet document** — `fleet_document.py` owns the `meridian.fleet/v1` schema, server-ref mapping, and `intent_changes()`, the one description of how two intents differ; it reports a reorder rather than ever returning no change for unequal intents.
- **Client limits** — `client_limits.py` parses binary traffic sizes (bounded by SQLite int64), UTC expiry/reset periods, and device caps (1–100/default) into `ClientLimitChange`. Explicit null clears expiry or uses the fleet cap; omitted fields preserve state. Device results compose IDs with full records whose nullable metadata fields are required, so private IDs never gain metadata during parsing.
- **Creation-only account settings** — `UserCreationSettings` is shared by service creation and `AccessIntent.creation_settings`; an empty mapping is omitted so existing user payloads and plans keep their hashes.
- **Calendars** — `calendar.py` shares clamped month shifts with billing: client-limit renewal previews add one or three months after max(now, expiry) in UTC and pass through the existing finite timestamp parser, and `server_calendar.py` preserves UTC reset/payment anchors through short months and leap years.
- **Server metadata** — `server_allowances.py` owns reviewed allowances, bounded provider labels, payment dates, and nullable measurement contracts; empty topology metadata is omitted.
- **Languages** — `languages.py` is the shared en/ru/fa/zh type; fleet defaults and nullable person overrides are operator settings, separate from CLI locale detection and reviewed topology. `FleetLanguage` adds `auto` without widening the four-language CLI/person type; shared resolution prefers the person, and only pages detect browsers, while automatic status remarks use English.
- **Operator notice** — `notices.py` owns one plain paragraph (500 Unicode characters, 1,024 after bounded expansion), five whole substitutions, an optional UTC expiry, and one preview/save request; braces never enable formatting or attribute access.
- **Owner contact** — `OwnerContactSettings` shares the optional reviewed contact across delivery intent, compiler payloads, and pages; empty defaults omit the field, text is one plain line up to 120 characters, and HTTPS/TG/mailto links canonicalize to bounded ASCII without a lookup.
- **Private person metadata** — `person_metadata.py` owns plain-text notes (500 Unicode characters, newlines allowed) and one uppercase A–Z/digit/underscore tag (1–16); empty strings clear, omitted edits preserve, and creation intent never contains them.
- **Subscription vocabulary** — `subscription_formats.py` owns the document/suffix vocabulary; `subscription_preview.py` bounds transient headers and exposes typed selection, included protocols, omission reasons, and redacted service headers without credentials.
- **Connection-page settings** — `connection_pages.py` owns branding and exit-country fields shared with service settings; empty defaults are omitted so existing JSON and old databases retain their defaults.
- **Privacy and usage contracts** — `control.models` owns settings, audit, and UTC day totals; command envelopes compose those same models. `ClientShowResult` carries optional seven-day usage, not a second history shape.
- **Traffic evidence** — `core/traffic.py` is the shared aggregate evidence contract: first accepted use and last reporting node per person, plus active people and collection/receipt times in each node's latest report; unknown historical evidence remains null.
- **Attention predicates** — `attention.py` derives typed person/node reasons from aggregate evidence, with bounded day/percentage thresholds and no stored alert flags; client list composes the same reasons when `--attention` is requested.
- **Local lifecycle facts** — `fleet_events.py` is the durable event contract for integrations: monotonic ID, UTC time, kind, actor, subject, and a closed detail model per kind; command progress keeps its separate `events.py` contract.

## What's done well
- **Defaults stay off the wire** — client routing has one `ru-split | full` type, and delivery refresh intervals one integer contract (1–168 hours); new intent and settings fields omit `ru-split` and the six-hour default, preserving existing serialized plans and payloads.
- **Command kinds are hello capabilities** — each kind is its own; successful `reset_inbound` results disclose `inbound_reset` or `core_restart` through the nullable `effect` field, without carrying a user identifier.
- No barrel re-exports in `__init__.py`; consumers import specific submodules.
- Shared serializers keep JSON output stable and recurse through core models.
- Fleet, client, node, and relay commands build typed, redacted results before any envelope or rendering.
- Reporter primitives let provision, apply, and deploy emit typed events without choosing a renderer.

## Pitfalls
- **Never emit raw SSH commands, panel tokens, private keys, database URLs, or subscription secrets.**
- **`redact_string()` is the only secret masker** (SSH debug logs included) — teach it a new secret form instead of adding a local regex.
- **Preview text redaction masks whole URIs** in operator labels and known credential material; opaque Base64 headers are replaced rather than passed through a plaintext masker.
- **Reveal exact paths only** — treat individual connection URI and SVG QR fields as complete credentials: client show reveals only their exact producing paths, never passive output or neighboring fields. JSON reveal options accept exact dotted paths, including list indices, and preserve authorized private note/tag fields without revealing neighboring URLs or credentials.
- **Generated JSON Schemas are public data** — redact values without destroying schema `properties`.
- **SSH passwords and private keys never enter public server contracts** — Engine handles them through short-lived secret channels.
- **Keep human wording in adapters** — core summaries are short API metadata.
- **Wrap Pydantic `ValidationError` before rendering** — raw model errors are too noisy for operators.
- **Use `WithJsonSchema` for custom validators** that need public schema constraints without replacing readable runtime errors.
- **Timestamp validates calendar dates and clock times after its wire-pattern check** — preserve the input string so fractional precision and generated schemas stay unchanged.
- **Keep topology IDs lowercase and bounded**, and validate access usernames against the pinned panel contract before compilation.
- **Routing domains are suffixes** — gateway rules and exit overrides validate with `validate_domain_suffix_value()` (bare zones like `ru`, `рф` → `xn--p1ai`); SNI and certificate names keep `validate_hostname_value()`, which needs two labels.
- **Provider labels allow Persian ZWNJ/ZWJ joiners** while rejecting line breaks, bidi controls, and other control characters.
- **Server metadata names only declared physical servers** — preserve it through setup drafts and fleet name/ref mapping; it never moves automatically with an exit.
- **Fleet diffs compare creation settings for each person in access-user order** — adding, editing, or removing that mapping is not a fleet reorder.
- **Command catalogs list only completed JSON outcomes** — an interruption exits 130 without an envelope, while a cooperative cancel is a completed `cancelled` outcome with exit 5.
- **Fleet results keep reachability, runtime, and convergence apart** — they carry live reachability, fresh typed runtime evidence, and convergence separately, so a live poll never makes an old health report available; a fresh healthy runtime can still be awaiting convergence, which fleet status counts separately and degrades on until `in_sync`, while stale reports remain unavailable evidence.
- **Client detail carries lifetime traffic and the current period's start** through the access adapter; resetting period use never erases the lifetime figure.
