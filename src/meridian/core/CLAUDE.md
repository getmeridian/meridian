# core — Meridian API contracts

## Design decisions
- **People queries have one typed request** — paging controls never change which people match a selection.
- **One account clock** — `account_schedule.py` computes UTC activation, anniversary refills, and clamped renewal catch-up; billing shares `calendar.monthly_period()`.
- **No CLI globals** — core never imports Typer, Rich, console, config, or command modules (tested); the CLI and the control service are adapters. Constants live in `core/defaults.py`.
- **Envelope first** — JSON clients get one stable `meridian.output/v1` shape with command data under `data`; `schema_envelopes.py` builds command envelopes from the catalog, and `schema.py` composes contract families from their owning modules.
- **Command metadata is schema-neutral** — `command_catalog.COMMAND_OUTPUTS` maps every public command path to its JSON output or `None`; argv, flags, and help come from the Typer tree via `meridian.cli_catalog`.
- **Events are a public set** — long-running flows report typed JSONL-ready events; add a name to `core.events` before emitting it. Durable lifecycle facts for integrations are a separate contract (`fleet_events.py`).
- **Pydantic at API boundaries** — requests, results, events, errors, and service contracts validate, serialize, and export JSON Schema from Pydantic v2 models; validate at the boundary, before any SSH or panel I/O.
- **Verification is four-state** — passed, failed, warning, skipped; skipped evidence is inconclusive, never a pass.
- **Cooperative cancellation** — `core/cancellation.py` owns the token protocol; workers poll only at safe points.
- **Agent protocol is a contract, kept apart** — `meridian.agent/v1` (`agent_*` modules) is the internal service-to-agent wire format the Rust agent consumes; `schema_agent.AGENT_SCHEMAS` exports it to `contracts/meridian/v1/agent/`, never into `core.schema`. Docstrings are its spec: every field always sent, no floats, no addresses in agent messages; extend it only behind a negotiated capability. `version_skew.py` holds CLI/service/agent compatibility.
- **Two names per person** — `AccessIntent.users` are Latin usernames; `display_names` keeps a typed name when it differs. `assign_access_usernames()` is the one place mapping typed names to usernames (via anyascii); look people up with `find_user()`.
- **Fleet document** — `fleet_document.py` owns `meridian.fleet/v1` and `intent_changes()`, the one description of how two intents differ; unequal intents always report a change, a reorder at least.
- **Operator settings have one contract each** — client limits (`client_limits.py`), creation settings (`UserCreationSettings`), languages, notices, owner contact, person metadata, server allowances, and connection-page settings; each module owns its bounds and canonical form. Explicit null clears, omitted fields preserve.
- **Derived evidence** — `traffic.py` is the aggregate evidence contract (unknown history stays null) and `attention.py` derives typed reasons from it with no stored alert flags; `calendar.py` and `server_calendar.py` share clamped UTC month arithmetic.
- **Subscription vocabulary** — `subscription_formats.py` owns document and suffix names; `subscription_preview.py` bounds transient headers and explains selection without credentials.

## What's done well
- **Defaults stay off the wire** — new intent and settings fields omit their defaults (routing `ru-split`, the six-hour refresh, empty mappings), so existing plans and payloads keep their hashes.
- **Command kinds are hello capabilities**, and a reset result discloses its effect without carrying a user identifier.
- No barrel re-exports in `__init__.py`; consumers import specific submodules.
- Commands build typed, redacted results before any envelope or rendering, and reporter primitives emit typed events without choosing a renderer.

## Pitfalls
- **Device cap zero is unlimited** — only null inherits the fleet cap; never resolve a cap by truthiness.
- **Never emit raw SSH commands, panel tokens, private keys, database URLs, or subscription secrets.**
- **`redact_string()` is the only secret masker** (SSH debug logs included) — teach it a new secret form instead of adding a local regex.
- **Reveal exact paths only** — connection URIs and QR fields are complete credentials; JSON reveal options name exact dotted paths (list indices included) and never expose neighbouring fields. Preview redaction masks whole URIs and replaces opaque Base64 headers.
- **Generated JSON Schemas are public data** — redact values without destroying schema `properties`.
- **SSH passwords and private keys never enter public server contracts.**
- **Keep human wording in adapters**, and wrap Pydantic `ValidationError` before rendering; raw model errors are too noisy for operators.
- **Use `WithJsonSchema` for custom validators** that need public schema constraints without replacing readable runtime errors.
- **Timestamps keep their input string** after validating date and time, so fractional precision and generated schemas stay unchanged.
- **Routing domains are suffixes** — validate with `validate_domain_suffix_value()` (bare zones like `ru`, IDN to punycode); SNI and certificate names use `validate_hostname_value()`, which needs two labels. Topology IDs stay lowercase and bounded.
- **Provider labels allow Persian ZWNJ/ZWJ joiners** while rejecting line breaks, bidi controls, and other control characters.
- **Server metadata names only declared physical servers** and never moves automatically with an exit.
- **Fleet diffs compare creation settings per person** — changing them is an edit, not a reorder.
- **Command catalogs list only completed JSON outcomes** — an interruption exits 130 without an envelope; a cooperative cancel is a `cancelled` outcome with exit 5.
- **Fleet results keep reachability, runtime, and convergence apart** — a live poll never makes an old health report current, and a healthy runtime can still await convergence.
- **Client detail carries lifetime traffic beside the period** — resetting period use never erases the lifetime figure.
- **Period directions sum to used traffic** — `PeriodTraffic.unattributed_bytes` names the part of download whose historical direction is unknown, never a third amount to add.
- **Never-used and stopped-use attention are disjoint** — `is_never_used` includes imported lifetime evidence; stopped use requires a known last traffic time, never a guessed import date or daily history.
- **Quota percentages are integer evidence** — floor exact byte ratios once for attention and browser triage, or JavaScript rounds a large cap across the threshold.
