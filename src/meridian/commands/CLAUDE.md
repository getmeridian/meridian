# commands — One module per subcommand

## Design decisions

**One file per command** — keeps concerns isolated. Each is a Typer sub-app registered in `cli.py`.
**Topology-first pattern** — fleet commands load `cluster.yml` through `load_topology_cluster()`, which refuses pre-topology files with rebuild guidance. Mutations (`node add`, `relay add`, `client add`) edit `topology_intent` and run the reviewed `SetupRuntime` apply; reads use the panel API plus `adapters/cluster.py`.
**One target vocabulary** — `cli_options.py` owns the shared `SERVER` argument and `--user`/`--ssh-port`/`--timeout`/`--yes` types; `meridian.resolve.parse_target()`/`merge_target()` parse `[user@]host[:port]`, saved names, and `local` like ssh, refusing flag contradictions. Empty user / zero port mean "saved, else root/22". `--server` is a hidden alias.
**Server resolution cascade** in `resolve.py` — adds CLI-specific Rich output around the shared helpers in `meridian.resolve`: 1. target (`local`, saved name/IP/ID, or literal address) → 2. local-mode detection → 3. single-server auto-select → 4. fail with candidates. Add commands (`node add`, `relay add`) resolve first, then validate their request with the resolved address, user, and port.
**Machine deploy mode** — `deploy --json`, `deploy --events=jsonl`, `deploy --request FILE`, and `deploy --dry-run` are process API surfaces for UI clients. Keep prompts and Rich output out; stdout is the final `meridian.output/v1` envelope and JSONL progress goes to stderr. Hidden `--cancel-file PATH` makes deploy stop at its next safe point and emit a `cancelled` envelope (exit 5) once the file exists.
**Health surface: check / test / doctor** — `health.py` (`check`) dispatches by fleet role to preflight, server, or relay scopes and returns one `HealthCheckResult`; `test` drives traffic (`--probe` for the censor view); `doctor` bundles a report. Every scope renders through `check_render.py`. Completed negative evidence exits 4, unavailable evidence exits 3, only full passes exit 0; `--basic` never accepts `--client`.
**Canonical connection test** — full `test` fetches one deterministic active client's delivered Xray JSON and executes its fallback plus each target outbound without rebuilding credentials or transports locally. A bare `test` with several saved servers runs once per public entry (exits, then relay entries) and returns `FleetTestResult`.
**Apply progress** — every command that runs `SetupRuntime` wraps it in `apply_progress()` (`apply_progress.py`): a spinner for the running action plus one line per change or failure, silent under `--json`/quiet; `deploy --events=jsonl` passes its JSONL reporter instead.
**V4 setup is presentation-only** — `setup_v4.py` and `setup_wizard.py` collect resumable choices; compilation, checkpointed apply, and canonical verification stay in `meridian.setup`.
**One wizard for everyone** — real terms plus one plain sentence each, all text from `meridian.i18n`; `wizard_prompts.py` owns navigation words, yes/no, numbered choices, and the language step. Smart defaults replace questions (exit IDs from server names, random transport paths, routing only with a gateway, delivery formats).
**Validate at entry** — command functions build core request models first, then render wrapped validation errors with `fail()` before opening SSH or panel connections.
**Sectioned help** — `cli.py` lists top-level commands in `_HELP_LAYOUT` panels (Get started, Clients, Servers, Check & fix, Advanced); `deploy` groups options into Server access, Methods, Branding, and Automation panels. Hidden aliases stay out of the layout and list last.
**Command groups**: `client` (add/show/list/remove/enable/disable), `node` (add/list/remove/replace), `relay` (add/list/remove), `route` (add/list/remove — per-exit egress overrides applied like `client add`), `fleet` (status/inventory/upgrade). Top-level: `setup`, `deploy`, `plan`, `apply`, `check`, `test`, `doctor`, `panel`, `teardown`. Hidden one-release aliases: `preflight`, `probe`, `node check`, `relay check`, `relay deploy`. `client remove`, `node remove`, and `relay remove` edit the intent and let reviewed retirement delete what vanished; `topology_removal.py` owns their shared review, confirmation, and apply; `node_replace.py` re-places one exit and lets reviewed retirement clean the old server. `relay_check.py` collects each compiled Realm hop over SSH plus the public entry locally.

## What's done well

- **`client add` follows topology ownership** — it expands declared access intent and applies the reviewed topology; it never creates panel users directly.
- **`deploy --dry-run` and Engine share one preview** — `setup/single_server.py::preview_single_server_deploy()` compiles the intent; neither path imports the other.
- **`fleet inventory`** — prints local topology with live panel status without exposing tokens.
- **`fleet upgrade` never mutates beside the plan** — it only reads images (`diagnostics/images.py`), then runs the reviewed apply of the unchanged intent (new pins ride the render contract) and the fleet `check`.
- **Read and health commands** — node, relay, fleet, and doctor consume registry-backed topology plus compiled listener allocations.

## Pitfalls
- **`console.fail()` always exits** — raises `typer.Exit` with semantic codes (user=2, system=3, bug=1). Only call from command entry points.
- **`confirm()` returns bool** — returns True on accept, False on reject. Callers must check `if not confirm(...): raise typer.Exit(1)`.
- **Topology deletion is guarded** — teardown and server removal check intent roles, active workloads, and allocations; only a whole single-server fleet tears down in one step.
- Removal commands refuse the control server and dependents (relays into an exit, gateways bridged through it) before review; `--force` on `node remove` only extends the edit to those relay chains.
- Post-mutation local save failures exit 3, say remote state changed, and preserve typed partial data where the command contract supports it.
- V4 client reads and connectivity tests expose only users declared in `topology_intent.access.users`; never fall back to panel-wide service users.
- Never render saved panel administrator credentials, except `meridian panel --show-password`, which exists so operators can manage Remnawave themselves. Handoff links (`HANDOFF_LINK_KEYS`) appear verbatim only in the final output of the command that produced them (`emit_json(..., reveal=...)`); logs, events, and errors stay redacted.
- Doctor reports pass every free-form line through `redact_string()` and render with Rich markup disabled before sharing.
- A bare word that is neither a saved name nor an address is refused, never dialed; keep `--timeout` a string so bad values still get the JSON error envelope.
- Do not use `model_copy(update=...)` to apply untrusted request data; rebuild the Pydantic request model.
- V4 plan/apply must bind the reviewed hash and emit typed compiled results or exactly one structured terminal error.
- The wizard needs a terminal: `prompt()` returns the default when `/dev/tty` is missing, so a headless run would loop on required answers; `setup` refuses without one unless `--yes`.
- `node replace` refuses the control server (subscription URLs would change) and a new server that already has any role; check roles before dialing.
- A failed `node add` leaves its exit in the intent; re-running it must resume that exit (refusing conflicting `--sni`/`--domain`), never add a second exit on the same server.
- A new public command needs an entry in `_HELP_LAYOUT` and a `rich_help_panel`, or it appears in a bare `Commands` list above the sections.
- Entry points convert library `MeridianError`s with `fail(exc)`; an uncaught one prints a traceback (`setup --intent MISSING` did).
- Page palettes live in `core.inputs.PAGE_PALETTES` (must match `app.js`, tested); `deploy` normalizes `--icon` with `branding.normalize_icon()` before any SSH, and a redeploy merges unnamed branding flags from the saved values.
