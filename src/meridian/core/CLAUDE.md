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
- **Narrow panel interfaces** - services depend on protocols such as `FleetPanelClient` and `ClientPanelClient`, not the concrete Remnawave client.
- **Two names per person** - `AccessIntent.users` are Remnawave usernames (Latin only); `display_names` keeps the typed name when it differs and is omitted when empty. `assign_access_usernames()` is the one place that maps typed names to usernames (known first, Latin names claim themselves, the rest transliterate via anyascii); look people up with `find_user()`.

## What's done well
- No barrel re-exports in `__init__.py`; consumers import specific submodules.
- Shared serializers keep JSON output stable and recurse through core models.
- Fleet, client, node, and relay commands build typed, redacted results before any envelope or rendering.
- Reporter primitives let provision, apply, and deploy emit typed events without choosing a renderer.

## Pitfalls
- Never emit raw SSH commands, panel tokens, private keys, database URLs, or subscription secrets.
- `redact_string()` is the only secret masker (SSH debug logs included); teach it a new secret form instead of adding a local regex.
- Generated JSON Schemas are public data; redact values without destroying schema `properties`.
- SSH passwords and private keys never enter public server contracts; Engine handles them through short-lived secret channels.
- Keep human wording in adapters; core summaries are short API metadata.
- Wrap Pydantic `ValidationError` before rendering; raw model errors are too noisy for operators.
- Use `WithJsonSchema` for custom validators that need public schema constraints without replacing readable runtime errors.
- Keep topology IDs lowercase and bounded, and validate access usernames against the pinned panel contract before compilation.
- Command catalogs list only completed JSON outcomes: an interruption exits 130 without an envelope, while a cooperative cancel is a completed `cancelled` outcome with exit 5.
